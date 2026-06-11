# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import logging

_logger = logging.getLogger(__name__)

# Master SaaS domain (this could ideally be stored in ir.config_parameter)
SAAS_MASTER_URL = 'https://saas.havano.pro'

class SaaS_SSO_Controller(http.Controller):

    @http.route('/saas/sso/login', type='http', auth='public', website=True, sitemap=False)
    def sso_login(self, token, **kwargs):
        """
        Receives the OTT from the Master, validates it server-to-server,
        and logs the user in if valid.
        """
        if not token:
            return request.render('web.login', {'error': 'Missing SSO Token'})

        # Validate token against SaaS Master
        try:
            # We use standard HTTP request to the master
            validate_url = f"{SAAS_MASTER_URL.rstrip('/')}/api/v1/sso/validate"
            response = requests.post(
                validate_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {"token": token}
                },
                timeout=10
            )
            response.raise_for_status()
            result = response.json().get('result', {})
            
            if not result.get('success', True) or result.get('error'):
                return request.render('web.login', {'error': f"SSO Validation Failed: {result.get('error')}"})

            user_data = result.get('data')
            if not user_data or not user_data.get('email'):
                return request.render('web.login', {'error': 'Invalid SSO data received from Master'})

        except Exception as e:
            _logger.exception("Failed to validate SSO token with Master")
            return request.render('web.login', {'error': 'Could not connect to authentication server'})

        email = user_data['email']
        name = user_data.get('name', email)

        # Find or create user locally
        user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
        if not user:
            groups_field = 'group_ids' if 'group_ids' in request.env['res.users']._fields else 'groups_id'
            user = request.env['res.users'].sudo().with_context(no_reset_password=True).create({
                'name': name,
                'login': email,
                'email': email,
                groups_field: [(6, 0, [request.env.ref('base.group_user').id, request.env.ref('base.group_erp_manager').id])]
            })
            # Force random password to secure the account locally
            import uuid
            user.password = uuid.uuid4().hex

        # Log the user in bypassing standard password check!
        request.session.should_rotate = True
        request.session.update({
            'db': request.env.registry.db_name,
            'login': user.login,
            'uid': user.id,
            'context': dict(request.env['res.users'].sudo().browse(user.id).context_get()),
            'session_token': user.sudo()._compute_session_token(request.session.sid),
        })

        # Save session to propagate changes
        request._save_session()

        # Redirect to the backend or provided redirect URL
        redirect_url = kwargs.get('redirect', '/web')
        return request.redirect(redirect_url)
