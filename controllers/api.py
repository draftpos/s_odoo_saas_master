import json
import logging
import secrets
from datetime import datetime, timedelta

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import AccessDenied, ValidationError

_logger = logging.getLogger(__name__)


class SaaSAPI(http.Controller):
    """
    REST API Controller for SaaS Master - Android App Integration

    All endpoints return JSON responses with consistent structure:
    {
        "success": true/false,
        "data": {...} or [...],
        "error": "error message" (only if success=false)
    }

    Authentication: API Key based
    - Pass API key in header: Authorization: Bearer <api_key>
    - Or as parameter: api_key=<api_key>
    """

    def _json_response(self, data=None, error=None, status=200):
        """Helper to create consistent JSON responses."""
        response_data = {
            'success': error is None,
            'data': data,
        }
        if error:
            response_data['error'] = error
        return response_data

    def _authenticate(self):
        """
        Authenticate request using API key.
        Returns partner if authenticated, raises AccessDenied otherwise.
        """
        api_key = None

        # Check Authorization header
        auth_header = request.httprequest.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            api_key = auth_header[7:]

        # Check parameter
        if not api_key:
            api_key = request.params.get('api_key')

        if not api_key:
            raise AccessDenied(_("API key is required. Pass it in Authorization header as 'Bearer <key>' or as 'api_key' parameter."))

        # Find API token
        token = request.env['saas.api.token'].sudo().search([
            ('token', '=', api_key),
            ('active', '=', True),
        ], limit=1)

        if not token:
            raise AccessDenied(_("Invalid or expired API key."))

        # Update last used
        token.write({'last_used': fields.Datetime.now()})

        return token.partner_id

    def _get_instance_data(self, instance):
        """Convert instance record to JSON-serializable dict."""
        return {
            'id': instance.id,
            'name': instance.name,
            'domain_name': instance.domain_name,
            'url': instance.url,
            'state': instance.state,
            'operation_state': instance.operation_state,
            'trial': instance.trial,
            'expiration_date': instance.expiration_date.isoformat() if instance.expiration_date else None,
            'subscription_type': instance.subscription_type,
            'odoo_version': instance.odoo_version_id.name if instance.odoo_version_id else None,
            'active_users': instance.active_user,
            'db_name': instance.db_name,
            'created_date': instance.create_date.isoformat() if instance.create_date else None,
        }

    # ==================== AUTH ENDPOINTS ====================

    @http.route('/api/v1/auth/register', type='json', auth='public', methods=['POST'], csrf=False)
    def api_register(self, **kwargs):
        """
        Register a new user and get API token.

        POST /api/v1/auth/register
        Body: {
            "name": "John Doe",
            "email": "john@example.com",
            "password": "securepassword",
            "phone": "optional"
        }

        Returns: {
            "success": true,
            "data": {
                "user_id": 1,
                "partner_id": 1,
                "api_key": "generated_api_key",
                "message": "Registration successful"
            }
        }
        """
        try:
            import re
            from email.utils import parseaddr

            name = kwargs.get('name')
            email = kwargs.get('email')
            password = kwargs.get('password')
            phone = kwargs.get('phone')

            if not email or not password:
                return self._json_response(error="Email and password are required.")

            # Hardened Validation
            email = email.strip()
            name_part, addr_part = parseaddr(email)
            if not addr_part or '@' not in addr_part:
                return self._json_response(error="Invalid email format.")

            if len(password) < 8:
                return self._json_response(error="Password must be at least 8 characters long.")

            if name:
                name = name.strip()
                if len(name) > 100 or re.search(r'[\x00-\x1f\x7f-\x9f<>]', name):
                    return self._json_response(error="Name contains invalid characters or is too long.")
            else:
                name = email.split('@')[0] if email else 'User'

            # Check if user exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing_user:
                return self._json_response(error="A user with this email is already registered (email is taken).")
            else:
                # Create user
                user_vals = {
                    'name': name,
                    'login': email,
                    'email': email,
                    'password': password,
                }
                if phone:
                    user_vals['phone'] = phone
    
                user = request.env['res.users'].sudo().with_context(
                    no_reset_password=True,
                    mail_create_nolog=True,
                    mail_create_nosubscribe=True,
                    mail_notrack=True
                ).create(user_vals)

            # Generate API token
            token = request.env['saas.api.token'].sudo().create({
                'partner_id': user.partner_id.id,
                'name': 'Mobile App Token',
            })

            # Check for pool assignment or fallback creation if subdomain is provided
            subdomain = kwargs.get('subdomain') or kwargs.get('sub_domain')
            base_domain_id = kwargs.get('base_domain_id')

            import secrets
            verification_token = secrets.token_urlsafe(32)

            partner = user.partner_id
            partner.write({
                'is_email_verified': False,
                'email_verification_token': verification_token,
                'pending_subdomain': subdomain or False,
                'pending_base_domain_id': base_domain_id or False,
            })

            # Send verification email
            try:
                verify_url = f"{request.httprequest.url_root.rstrip('/')}/saas/verify_email?token={verification_token}"
                subject = "Verify your email for Havano SaaS"
                body = f"""
                <div style="margin:0px;padding:24px;font-family:'Inter',sans-serif;background-color:#F8F9FA;color:#333;line-height:1.6;">
                    <div style="max-width:600px;margin:0 auto;background:#FFF;padding:32px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.05);">
                        <h2 style="color:#2C3E50;margin-top:0;">Verify Your Email Address</h2>
                        <p>Hello {name},</p>
                        <p>Thank you for registering! Please click the button below to verify your email address. Once verified, your Odoo instance will be automatically provisioned and deployed for you.</p>
                        <p style="text-align:center;margin:32px 0;">
                            <a href="{verify_url}" style="background-color:#875A7B;padding:12px 28px;text-decoration:none;color:#FFF;border-radius:6px;font-weight:600;display:inline-block;">Verify Email Address</a>
                        </p>
                        <p style="font-size:12px;color:#7F8C8D;">Or copy and paste this link in your browser:</p>
                        <p style="font-size:12px;color:#3498DB;word-break:break-all;"><a href="{verify_url}">{verify_url}</a></p>
                        <hr style="border:none;border-top:1px solid #ECF0F1;margin:24px 0;"/>
                        <p style="font-size:12px;color:#95A5A6;margin-bottom:0;">Best regards,<br/>Havano POS Team</p>
                    </div>
                </div>
                """
                mail_values = {
                    'subject': subject,
                    'body_html': body,
                    'email_to': email,
                    'email_from': request.env.company.email or "noreply@havano.pro",
                }
                request.env['mail.mail'].sudo().create(mail_values).send()
            except Exception as mail_err:
                _logger.warning("Failed to send verification email to %s: %s", email, mail_err)

            res_data = {
                'user_id': user.id,
                'partner_id': user.partner_id.id,
                'api_key': token.token,
                'message': 'Registration successful. Please check your email to verify your account and activate your site.',
            }

            return self._json_response(data=res_data)

        except AccessDenied as e:
            ip = request.httprequest.remote_addr
            _logger.warning("Failed registration/login attempt from IP %s for email %s. Error: %s", ip, email, e)
            return self._json_response(error=str(e))
        except ValidationError as e:
            ip = request.httprequest.remote_addr
            _logger.warning("Failed registration attempt validation from IP %s for email %s. Error: %s", ip, email, e)
            return self._json_response(error=str(e))
        except Exception as e:
            ip = request.httprequest.remote_addr
            _logger.warning("Failed registration system error from IP %s for email %s. Error: %s", ip, email, e)
            _logger.exception("Registration error")
            return self._json_response(error=str(e))

    @http.route('/saas/verify_email', type='http', auth='public', website=True)
    def verify_email(self, token, **kwargs):
        if not token:
            return request.make_response("Missing verification token.", headers=[('Content-Type', 'text/html')])

        partner = request.env['res.partner'].sudo().search([
            ('email_verification_token', '=', token)
        ], limit=1)

        if not partner:
            return request.make_response("Invalid or expired verification token.", headers=[('Content-Type', 'text/html')])

        subdomain = partner.pending_subdomain
        base_domain_id = partner.pending_base_domain_id.id if partner.pending_base_domain_id else False

        # Mark email as verified and clear token/pending fields
        partner.write({
            'is_email_verified': True,
            'email_verification_token': False,
            'pending_subdomain': False,
            'pending_base_domain_id': False,
        })

        # Claim or create instance
        user = request.env['res.users'].sudo().search([('partner_id', '=', partner.id)], limit=1)
        password_hash = user.password if user else False

        claimed_instance = request.env['saas.odoo.instance'].sudo()._try_claim_pool_instance(
            partner=partner,
            password_hash=password_hash
        )

        if not claimed_instance and subdomain and base_domain_id:
            try:
                base_domain = request.env['saas.based.domain'].sudo().browse(base_domain_id)
                odoo_server = request.env['saas.odoo.server'].sudo().search([
                    ('active', '=', True),
                ], order='sequence')
                odoo_server = odoo_server.filtered(lambda s: s.has_available_capacity())[:1]

                if odoo_server and base_domain.exists():
                    # Check trial limit
                    trial_limit = partner.company_id.limit_trial or 1
                    trial_count = request.env['saas.odoo.instance'].sudo().search_count([
                        ('partner_id', '=', partner.id),
                        ('trial', '=', True),
                        ('state', '!=', 'cancel'),
                    ])
                    if trial_count < trial_limit:
                        company = partner.company_id or request.env.company
                        creation_mode = 'scratch'
                        use_template = False
                        template_instance_id = False
                        if company.backup_restore_instance_id:
                            creation_mode = 'backup_restore'
                            use_template = True
                            template_instance_id = company.backup_restore_instance_id.id

                        instance_vals = {
                            'name': subdomain,
                            'based_domain_id': base_domain_id,
                            'odoo_version_id': odoo_server.odoo_version_id.id if odoo_server.odoo_version_id else request.env['saas.odoo.version'].sudo().search([], limit=1).id,
                            'odoo_server_id': odoo_server.id,
                            'partner_id': partner.id,
                            'trial': True,
                            'creation_mode': creation_mode,
                            'use_template': use_template,
                            'template_instance_id': template_instance_id,
                        }

                        # Create trial days
                        trial_days = partner.company_id.instance_trial_day or 15
                        instance_vals['expiration_date'] = fields.Date.today() + timedelta(days=trial_days)

                        instance = request.env['saas.odoo.instance'].sudo().create(instance_vals)

                        # Auto-deploy in background
                        import threading
                        import odoo as _odoo
                        
                        def _deploy_instance_async(db_name, instance_id):
                            registry = _odoo.registry(db_name)
                            with registry.cursor() as cr:
                                env = _odoo.api.Environment(cr, _odoo.SUPERUSER_ID, {})
                                try:
                                    inst = env['saas.odoo.instance'].browse(instance_id)
                                    inst.action_deploy()
                                    env.cr.commit()
                                except Exception as deploy_err:
                                    _logger.exception("Async deploy failed for instance %s: %s", instance_id, deploy_err)
                                    env.cr.rollback()

                        threading.Thread(target=_deploy_instance_async, args=(request.db, instance.id)).start()
            except Exception as creation_err:
                _logger.warning("Could not auto-create Odoo instance after verification: %s", creation_err)

        # Render a beautiful success screen
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Account Verified - Havano SaaS</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
            <style>
                body {
                    font-family: 'Inter', sans-serif;
                    background-color: #f4f6f9;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .container {
                    background: white;
                    padding: 40px;
                    border-radius: 12px;
                    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
                    text-align: center;
                    max-width: 450px;
                    width: 100%;
                }
                h1 {
                    color: #2c3e50;
                    font-size: 24px;
                    margin-bottom: 16px;
                }
                p {
                    color: #7f8c8d;
                    font-size: 16px;
                    line-height: 1.5;
                    margin-bottom: 24px;
                }
                .btn {
                    background-color: #875A7B;
                    color: white;
                    padding: 12px 24px;
                    text-decoration: none;
                    border-radius: 6px;
                    font-weight: 600;
                    display: inline-block;
                    transition: background-color 0.2s;
                }
                .btn:hover {
                    background-color: #6a4661;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="margin-bottom: 20px;">
                    <circle cx="12" cy="12" r="11" fill="#2ECC71" stroke="#27AE60" stroke-width="2"/>
                    <path d="M7 12L10.5 15.5L17 9" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                <h1>Email Verified Successfully!</h1>
                <p>Thank you for verifying your email address. Your Odoo instance is being assigned and deployed now. You can check the status and log in from your portal.</p>
                <a href="/my/saas/odoo-instances" class="btn">Go to Dashboard Portal</a>
            </div>
        </body>
        </html>
        """
        return request.make_response(html_content, headers=[('Content-Type', 'text/html')])

    @http.route('/api/v1/auth/login', type='json', auth='public', methods=['POST'], csrf=False)
    def api_login(self, **kwargs):
        """
        Login and get API token.

        POST /api/v1/auth/login
        Body: {
            "email": "john@example.com",
            "password": "password"
        }

        Returns: {
            "success": true,
            "data": {
                "user_id": 1,
                "partner_id": 1,
                "api_key": "api_key",
                "name": "John Doe"
            }
        }
        """
        try:
            email = kwargs.get('email')
            password = kwargs.get('password')
            db = kwargs.get('db') or request.db

            if not email or not password:
                return self._json_response(error="Email and password are required.")

            if not db:
                return self._json_response(error="Database not selected. Please pass 'db' in params or X-Odoo-Database header.")

            # Find the user first to check active and verified status
            try:
                from contextlib import ExitStack
                with ExitStack() as stack:
                    if not request.db or request.db != db:
                        cr = stack.enter_context(odoo.registry(db).cursor())
                        env = odoo.api.Environment(cr, None, {})
                    else:
                        env = request.env

                    # Search including inactive users
                    user_record = env['res.users'].sudo().with_context(active_test=False).search([('login', '=', email)], limit=1)
                    if not user_record:
                        return self._json_response(error="Invalid email or password.")
                    
                    if not user_record.active:
                        return self._json_response(error="Your account is deactivated. Please contact support.")
                    
                    if not user_record.partner_id.is_email_verified:
                        return self._json_response(error="Your email is not verified. Please check your email for the verification link.")
                    
                    # Authenticate
                    credential = {'login': email, 'password': password, 'type': 'password'}
                    auth_info = request.session.authenticate(env, credential)
                    uid = auth_info.get('uid')
            except AccessDenied:
                return self._json_response(error="Invalid email or password.")
            except Exception as e:
                _logger.exception("Login failed for %s on db %s", email, db)
                return self._json_response(error=f"Invalid email or password. (Details: {str(e)})")

            if not uid:
                return self._json_response(error="Invalid email or password.")

            user = request.env['res.users'].sudo().browse(uid)

            # Get or create API token
            token = request.env['saas.api.token'].sudo().search([
                ('partner_id', '=', user.partner_id.id),
                ('active', '=', True),
            ], limit=1)

            if not token:
                token = request.env['saas.api.token'].sudo().create({
                    'partner_id': user.partner_id.id,
                    'name': 'Mobile App Token',
                })

            instance = request.env['saas.odoo.instance'].sudo().search([
                ('partner_id', '=', user.partner_id.id),
                ('state', '=', 'deploy'),
            ], limit=1)
            site_url = instance.url if instance else ""

            return self._json_response(data={
                'user_id': user.id,
                'partner_id': user.partner_id.id,
                'api_key': token.token,
                'name': user.name,
                'email': user.email or user.login,
                'site': site_url,
                'active': user.active,
            })

        except Exception as e:
            _logger.exception("Login error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/auth/logout', type='json', auth='public', methods=['POST'], csrf=False)
    def api_logout(self, **kwargs):
        """
        Logout and invalidate API token.

        POST /api/v1/auth/logout
        Header: Authorization: Bearer <api_key>
        """
        try:
            partner = self._authenticate()

            # Deactivate token
            api_key = request.httprequest.headers.get('Authorization', '')[7:]
            if not api_key:
                api_key = request.params.get('api_key')

            token = request.env['saas.api.token'].sudo().search([
                ('token', '=', api_key),
            ], limit=1)

            if token:
                token.write({'active': False})

            return self._json_response(data={'message': 'Logged out successfully'})

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Logout error")
            return self._json_response(error=str(e))

    # ==================== INSTANCE ENDPOINTS ====================

    @http.route(['/api/v1/instances', '/api/v1/instances/list'], type='json', auth='public', methods=['GET', 'POST'], csrf=False)
    def api_instances(self, **kwargs):
        """
        GET: List user's instances
        POST: Create new instance

        GET /api/v1/instances
        Header: Authorization: Bearer <api_key>

        POST /api/v1/instances
        Header: Authorization: Bearer <api_key>
        Body: {
            "subdomain": "mycompany",
            "base_domain_id": 1,
            "odoo_server_id": 1 (optional, auto-selects if not provided),
            "apps": ["sale", "purchase"] (optional),
            "trial": true/false (optional, default false)
        }
        """
        try:
            partner = self._authenticate()

            if request.httprequest.method == 'GET' or 'list' in request.httprequest.path or kwargs.get('action') == 'list':
                # List instances
                instances = request.env['saas.odoo.instance'].sudo().search([
                    ('partner_id', '=', partner.id),
                ])

                return self._json_response(data={
                    'instances': [self._get_instance_data(i) for i in instances],
                    'count': len(instances),
                })

            elif request.httprequest.method == 'POST':
                # Create instance
                subdomain = kwargs.get('subdomain')
                base_domain_id = kwargs.get('base_domain_id')
                odoo_server_id = kwargs.get('odoo_server_id')
                apps = kwargs.get('apps', [])
                is_trial = kwargs.get('trial', False)
                restore = kwargs.get('restore', True)
                restore_source_site_id = kwargs.get('restore_source_site_id')

                if not subdomain:
                    return self._json_response(error="Subdomain is required.")

                if not base_domain_id:
                    return self._json_response(error="Base domain ID is required.")

                # Hardened validation
                subdomain = subdomain.strip()
                if len(subdomain) > 100:
                    return self._json_response(error="Subdomain name is too long.")
                import re
                if re.search(r'[\x00-\x1f\x7f-\x9f<>]', subdomain):
                    return self._json_response(error="Subdomain contains invalid characters.")

                # Validate subdomain
                if not subdomain.replace('-', '').isalnum():
                    return self._json_response(error="Subdomain can only contain letters, numbers, and hyphens.")
                if subdomain[0].isdigit() or subdomain[0] == '-':
                    return self._json_response(error="Subdomain must start with a letter.")

                # 1. Pool-first check: Try to claim an unassigned instance first!
                user = request.env['res.users'].sudo().search([('partner_id', '=', partner.id)], limit=1)
                password_hash = user.password if user else False
                
                claimed_instance = request.env['saas.odoo.instance'].sudo()._try_claim_pool_instance(
                    partner=partner,
                    password_hash=password_hash
                )
                
                if claimed_instance:
                    if is_trial:
                        trial_days = partner.company_id.instance_trial_day or 15
                        expiration_date = fields.Date.today() + timedelta(days=trial_days)
                    else:
                        expiration_date = request.env['saas.odoo.instance']._get_expiration_date('yearly', trial=False)
                        
                    default_module = request.env['saas.odoo.instance']._get_default_modules(apps)
                    claimed_instance.write({
                        'trial': is_trial,
                        'expiration_date': expiration_date,
                        'default_module': default_module,
                    })
                    
                    return self._json_response(data={
                        'instance': self._get_instance_data(claimed_instance),
                        'from_pool': True,
                        'message': 'Instance assigned successfully from pool.',
                    })

                # Fallback to fresh/template deployment
                # Check domain availability
                base_domain = request.env['saas.based.domain'].sudo().browse(base_domain_id)
                if not base_domain.exists():
                    return self._json_response(error="Invalid base domain ID.")

                full_domain = f"{subdomain}.{base_domain.name}"
                existing = request.env['saas.odoo.instance'].sudo().search([
                    ('domain_name', '=', full_domain)
                ], limit=1)
                if existing:
                    return self._json_response(error=f"Domain {full_domain} is already taken.")

                # Get or auto-select odoo server
                if odoo_server_id:
                    odoo_server = request.env['saas.odoo.server'].sudo().browse(odoo_server_id)
                    if not odoo_server.exists():
                        return self._json_response(error="Invalid Odoo server ID.")
                    if not odoo_server.has_available_capacity():
                        return self._json_response(error="Selected server has reached its instance limit.")
                else:
                    # Auto-select server with available capacity
                    odoo_server = request.env['saas.odoo.server'].sudo().search([
                        ('active', '=', True),
                    ], order='sequence')

                    odoo_server = odoo_server.filtered(lambda s: s.has_available_capacity())[:1]
                    if not odoo_server:
                        return self._json_response(error="No servers available with capacity.")

                # Check trial limit
                if is_trial:
                    trial_limit = partner.company_id.limit_trial or 1
                    trial_count = request.env['saas.odoo.instance'].sudo().search_count([
                        ('partner_id', '=', partner.id),
                        ('trial', '=', True),
                        ('state', '!=', 'cancel'),
                    ])
                    if trial_count >= trial_limit:
                        return self._json_response(error=f"You have reached the maximum trial limit ({trial_limit}).")

                creation_mode = 'scratch'
                use_template = False
                template_instance_id = False
                if restore:
                    creation_mode = 'backup_restore'
                    if restore_source_site_id:
                        template_record = request.env['saas.odoo.instance'].sudo().browse(int(restore_source_site_id))
                        if template_record.exists() and template_record.is_template and template_record.state == 'deploy':
                            use_template = True
                            template_instance_id = template_record.id
                    
                    if not use_template:
                        company = partner.company_id or request.env.company
                        if company.backup_restore_instance_id:
                            use_template = True
                            template_instance_id = company.backup_restore_instance_id.id
                        else:
                            creation_mode = 'scratch'

                # Prepare instance values
                instance_vals = {
                    'name': subdomain,
                    'based_domain_id': base_domain_id,
                    'odoo_version_id': odoo_server.odoo_version_id.id if odoo_server.odoo_version_id else request.env['saas.odoo.version'].sudo().search([], limit=1).id,
                    'odoo_server_id': odoo_server.id,
                    'partner_id': partner.id,
                    'trial': is_trial,
                    'creation_mode': creation_mode,
                    'use_template': use_template,
                    'template_instance_id': template_instance_id,
                }

                if apps:
                    instance_vals['default_module'] = ','.join(apps)

                if is_trial:
                    trial_days = partner.company_id.instance_trial_day or 15
                    instance_vals['expiration_date'] = fields.Date.today() + timedelta(days=trial_days)

                # Create instance
                instance = request.env['saas.odoo.instance'].sudo().create(instance_vals)

                # Auto-deploy in background
                import threading
                import odoo as _odoo
                
                def _deploy_instance_async(db_name, instance_id):
                    registry = _odoo.registry(db_name)
                    with registry.cursor() as cr:
                        env = _odoo.api.Environment(cr, _odoo.SUPERUSER_ID, {})
                        try:
                            instance = env['saas.odoo.instance'].browse(instance_id)
                            instance.action_deploy()
                            env.cr.commit()
                        except Exception as e:
                            _logger.exception("Async deploy failed for instance %s", instance_id)
                            env.cr.rollback()
                            try:
                                # Re-fetch instance after rollback to save the error
                                inst = env['saas.odoo.instance'].browse(instance_id)
                                inst.write({
                                    'error_message': str(e),
                                    'operation_state': 'stop'
                                })
                                env.cr.commit()
                            except Exception:
                                pass

                threading.Thread(target=_deploy_instance_async, args=(request.db, instance.id)).start()

                return self._json_response(data={
                    'instance': self._get_instance_data(instance),
                    'message': 'Instance created successfully. Deployment is running in the background.',
                })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except ValidationError as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>', type='json', auth='public', methods=['GET', 'DELETE'], csrf=False)
    def api_instance_detail(self, instance_id, **kwargs):
        """
        GET: Get instance details
        DELETE: Cancel instance

        GET /api/v1/instances/<id>
        DELETE /api/v1/instances/<id>
        """
        try:
            partner = self._authenticate()

            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            if request.httprequest.method == 'GET':
                return self._json_response(data={
                    'instance': self._get_instance_data(instance),
                })

            elif request.httprequest.method == 'DELETE':
                if instance.state == 'cancel':
                    return self._json_response(error="Instance is already cancelled.")

                instance.action_cancel()
                return self._json_response(data={
                    'message': 'Instance cancelled successfully',
                })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance detail API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>/sso', type='json', auth='public', methods=['POST'], csrf=False)
    def api_instance_sso(self, instance_id, **kwargs):
        """Generate a One-Time Token (OTT) magic link for SSO into the instance."""
        try:
            partner = self._authenticate()

            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            if instance.state != 'deploy':
                return self._json_response(error="Instance is not fully deployed yet. Please wait.")

            # Identify the user making the request from the partner
            user = request.env['res.users'].sudo().search([('partner_id', '=', partner.id)], limit=1)

            # Generate SSO token
            token = request.env['saas.sso.token'].sudo().create({
                'partner_id': partner.id,
                'user_id': user.id if user else False,
                'instance_id': instance.id,
            })

            sso_url = f"{instance.url}/saas/sso/login?token={token.token}"
            
            return self._json_response(data={
                'sso_url': sso_url,
                'token': token.token
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("SSO Token Generation error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/sso/validate', type='json', auth='public', methods=['POST'], csrf=False)
    def api_sso_validate(self, **kwargs):
        """Validate a One-Time Token (OTT) - Called by the Tenant Server."""
        try:
            token_str = kwargs.get('token')
            if not token_str:
                return self._json_response(error="Token is required.")

            token = request.env['saas.sso.token'].sudo().search([('token', '=', token_str)], limit=1)
            
            if not token:
                return self._json_response(error="Invalid or expired token.")
                
            from datetime import datetime
            if token.expiration_date < datetime.now():
                token.unlink()
                return self._json_response(error="Token has expired.")

            user_data = {
                'name': token.user_id.name if token.user_id else token.partner_id.name,
                'email': token.user_id.login if token.user_id else (token.partner_id.email or ''),
            }

            # Invalidate/Destroy token after first use!
            token.unlink()

            return self._json_response(data=user_data)

        except Exception as e:
            _logger.exception("SSO Token Validation error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/sso/validate_api_key', type='json', auth='public', methods=['POST'], csrf=False)
    def api_sso_validate_api_key(self, **kwargs):
        """Validate a Master API Key - Called by the Tenant Server."""
        try:
            api_key = kwargs.get('api_key')
            if not api_key:
                return self._json_response(error="API key is required.")

            uid = request.env['res.users.apikeys'].sudo()._check_credentials(
                scope='rpc',
                key=api_key
            )
            
            if not uid:
                return self._json_response(error="Invalid Master API key.")
                
            user = request.env['res.users'].sudo().browse(uid)
            if not user or not user.active:
                return self._json_response(error="User is inactive or deleted.")

            user_data = {
                'name': user.name,
                'email': user.login,
            }

            return self._json_response(data=user_data)

        except Exception as e:
            _logger.exception("SSO API Key Validation error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>/deploy', type='json', auth='public', methods=['POST'], csrf=False)
    def api_instance_deploy(self, instance_id, **kwargs):
        """Deploy an instance that is currently in draft state."""
        try:
            partner = self._authenticate()

            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            if instance.state != 'draft':
                return self._json_response(error=f"Instance cannot be deployed from state '{instance.state}'.")

            # Auto-deploy in background
            import threading
            import odoo
            
            def _deploy_instance_async(db_name, i_id):
                registry = odoo.registry(db_name)
                with registry.cursor() as cr:
                    env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                    try:
                        inst = env['saas.odoo.instance'].browse(i_id)
                        inst.write({'error_message': False})  # Clear previous errors
                        inst.action_deploy()
                        env.cr.commit()
                    except Exception as e:
                        _logger.exception("Async deploy failed for instance %s", i_id)
                        env.cr.rollback()
                        try:
                            # Re-fetch instance after rollback to save the error
                            inst = env['saas.odoo.instance'].browse(i_id)
                            inst.write({
                                'error_message': str(e),
                                'operation_state': 'stop'
                            })
                            env.cr.commit()
                        except Exception:
                            pass

            threading.Thread(target=_deploy_instance_async, args=(request.db, instance.id)).start()

            return self._json_response(data={
                'message': 'Deployment has been started in the background.',
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance API deploy error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>/status', type='json', auth='public', methods=['GET', 'POST'], csrf=False)
    def api_instance_status(self, instance_id, **kwargs):
        """Get the current deployment status, step, and error message of an instance."""
        try:
            partner = self._authenticate()
            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            return self._json_response(data={
                'id': instance.id,
                'name': instance.name,
                'state': instance.state,
                'operation_state': instance.operation_state,
                'deployment_step': instance.deployment_step,
                'error_message': instance.error_message,
                'is_reachable': instance.is_reachable,
            })
        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance API status error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>/start', type='json', auth='public', methods=['POST'], csrf=False)
    def api_instance_start(self, instance_id, **kwargs):
        """Start instance containers."""
        try:
            partner = self._authenticate()

            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            if instance.state != 'deploy':
                return self._json_response(error="Instance is not deployed.")

            instance.action_start()
            return self._json_response(data={
                'message': 'Instance started successfully',
                'operation_state': instance.operation_state,
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance start API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>/stop', type='json', auth='public', methods=['POST'], csrf=False)
    def api_instance_stop(self, instance_id, **kwargs):
        """Stop instance containers."""
        try:
            partner = self._authenticate()

            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            if instance.state != 'deploy':
                return self._json_response(error="Instance is not deployed.")

            instance.action_stop()
            return self._json_response(data={
                'message': 'Instance stopped successfully',
                'operation_state': instance.operation_state,
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance stop API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>/restart', type='json', auth='public', methods=['POST'], csrf=False)
    def api_instance_restart(self, instance_id, **kwargs):
        """Restart instance containers."""
        try:
            partner = self._authenticate()

            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            if instance.state != 'deploy':
                return self._json_response(error="Instance is not deployed.")

            instance.action_restart()
            return self._json_response(data={
                'message': 'Instance restarted successfully',
                'operation_state': instance.operation_state,
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance restart API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/instances/<int:instance_id>/backup', type='json', auth='public', methods=['POST', 'GET'], csrf=False)
    def api_instance_backup(self, instance_id, **kwargs):
        """
        GET: List backups
        POST: Create backup
        """
        try:
            partner = self._authenticate()

            instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
            if not instance.exists() or instance.partner_id.id != partner.id:
                return self._json_response(error="Instance not found.")

            if request.httprequest.method == 'GET':
                backups = instance.backup_ids.sorted('datetime', reverse=True)
                return self._json_response(data={
                    'backups': [{
                        'id': b.id,
                        'name': b.name,
                        'datetime': b.datetime.isoformat() if b.datetime else None,
                        'file_size': b.file_size,
                    } for b in backups],
                })

            elif request.httprequest.method == 'POST':
                if instance.state != 'deploy':
                    return self._json_response(error="Instance is not deployed.")

                instance.action_backup()
                return self._json_response(data={
                    'message': 'Backup created successfully',
                })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Instance backup API error")
            return self._json_response(error=str(e))

    # ==================== CONFIGURATION ENDPOINTS ====================

    @http.route('/api/v1/domains', type='json', auth='public', methods=['GET'], csrf=False)
    def api_domains(self, **kwargs):
        """Get available base domains."""
        try:
            self._authenticate()

            domains = request.env['saas.based.domain'].sudo().search([('active', '=', True)])
            return self._json_response(data={
                'domains': [{
                    'id': d.id,
                    'name': d.name,
                } for d in domains],
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Domains API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/domains/check', type='json', auth='public', methods=['POST'], csrf=False)
    def api_check_domain(self, **kwargs):
        """
        Check if subdomain is available.

        POST /api/v1/domains/check
        Body: {
            "subdomain": "mycompany",
            "base_domain_id": 1
        }
        """
        try:
            self._authenticate()

            subdomain = kwargs.get('subdomain')
            base_domain_id = kwargs.get('base_domain_id')

            if not subdomain or not base_domain_id:
                return self._json_response(error="Subdomain and base_domain_id are required.")

            base_domain = request.env['saas.based.domain'].sudo().browse(base_domain_id)
            if not base_domain.exists():
                return self._json_response(error="Invalid base domain.")

            full_domain = f"{subdomain}.{base_domain.name}"

            # Check instance
            existing = request.env['saas.odoo.instance'].sudo().search([
                ('domain_name', '=', full_domain)
            ], limit=1)

            # Check domain names
            existing_domain = request.env['saas.odoo.instance.domain.name'].sudo().search([
                ('name', '=', full_domain)
            ], limit=1)

            available = not existing and not existing_domain

            return self._json_response(data={
                'subdomain': subdomain,
                'base_domain': base_domain.name,
                'full_domain': full_domain,
                'available': available,
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Check domain API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/servers', type='json', auth='public', methods=['GET'], csrf=False)
    def api_servers(self, **kwargs):
        """Get available servers with capacity info."""
        try:
            self._authenticate()

            servers = request.env['saas.odoo.server'].sudo().search([('active', '=', True)])
            return self._json_response(data={
                'servers': [{
                    'id': s.id,
                    'name': s.name,
                    'odoo_version': s.odoo_version_id.name if s.odoo_version_id else None,
                    'instance_count': s.instance_count,
                    'max_instances': s.max_instances,
                    'available_slots': s.available_slots,
                    'has_capacity': s.has_available_capacity(),
                } for s in servers],
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Servers API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/apps', type='json', auth='public', methods=['GET'], csrf=False)
    def api_apps(self, **kwargs):
        """Get available apps/modules that can be installed."""
        try:
            self._authenticate()

            # Get SaaS app products
            products = request.env['product.product'].sudo().search([
                ('saas_app', '=', True),
            ])

            return self._json_response(data={
                'apps': [{
                    'id': p.id,
                    'name': p.name,
                    'technical_name': p.saas_technical_name,
                    'price': p.list_price,
                } for p in products],
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Apps API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/profile', type='json', auth='public', methods=['GET'], csrf=False)
    def api_profile(self, **kwargs):
        """Get current user profile."""
        try:
            partner = self._authenticate()

            return self._json_response(data={
                'partner_id': partner.id,
                'name': partner.name,
                'email': partner.email,
                'phone': partner.phone,
                'instance_count': partner.instance_count if hasattr(partner, 'instance_count') else 0,
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("Profile API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/saas/plans', type='json', auth='public', methods=['GET'], csrf=False)
    def api_saas_plans(self, **kwargs):
        """
        List all active SaaS plans with their details and calculated prices.
        
        GET /api/v1/saas/plans
        Header: Authorization: Bearer <api_key>
        """
        try:
            partner = self._authenticate()
            plans = request.env['saas.plan'].sudo().search([('active', '=', True)], order='sequence, id')
            
            pricelist = request.env['product.pricelist'].sudo().search([('company_id', '=', partner.company_id.id)], limit=1)
            if not pricelist:
                pricelist = request.env['product.pricelist'].sudo().search([], limit=1)

            plan_data = []
            for plan in plans:
                monthly_price = 0.0
                yearly_price = 0.0
                if plan.monthly_product_id:
                    monthly_price = pricelist.with_context(subscription_type='monthly')._get_product_price(plan.monthly_product_id, 1, partner=partner)
                if plan.yearly_product_id:
                    yearly_price = pricelist.with_context(subscription_type='yearly')._get_product_price(plan.yearly_product_id, 1, partner=partner)

                plan_data.append({
                    'id': plan.id,
                    'name': plan.name,
                    'code': plan.code,
                    'description': plan.description,
                    'limit_pos_terminals': plan.limit_pos_terminals,
                    'limit_users': plan.limit_users,
                    'monthly_product_id': plan.monthly_product_id.id if plan.monthly_product_id else False,
                    'yearly_product_id': plan.yearly_product_id.id if plan.yearly_product_id else False,
                    'monthly_price': monthly_price,
                    'yearly_price': yearly_price,
                })

            return self._json_response(data={
                'plans': plan_data,
                'currency': pricelist.currency_id.name,
                'currency_symbol': pricelist.currency_id.symbol,
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("SaaS plans API error")
            return self._json_response(error=str(e))

    @http.route('/api/v1/saas/subscribe', type='json', auth='public', methods=['POST'], csrf=False)
    def api_saas_subscribe(self, **kwargs):
        """
        Create/finish subscription for a plan (for new or existing instance).
        
        POST /api/v1/saas/subscribe
        Header: Authorization: Bearer <api_key>
        Body: {
            "plan_id": 1,
            "price_by": "yearly" or "monthly",
            "sub_domain": "mycompany" (required if new),
            "base_domain_id": 1 (required if new),
            "creation_mode": "scratch" or "backup_restore" (optional),
            "template_instance_id": 1 (optional),
            "instance_id": 1 (optional, for upgrade/downgrade)
        }
        """
        try:
            partner = self._authenticate()
            plan_id = kwargs.get('plan_id')
            price_by = kwargs.get('price_by', 'yearly')
            instance_id = kwargs.get('instance_id')

            if not plan_id:
                return self._json_response(error="plan_id is required.")

            plan = request.env['saas.plan'].sudo().browse(int(plan_id))
            if not plan.exists():
                return self._json_response(error="Plan not found.")

            pricelist = request.env['product.pricelist'].sudo().search([('company_id', '=', partner.company_id.id)], limit=1)
            if not pricelist:
                pricelist = request.env['product.pricelist'].sudo().search([], limit=1)

            checkout_vals = {
                'plan_id': plan.id,
                'subscription_type': price_by,
                'pricelist': pricelist,
                'partner': partner,
            }

            if instance_id:
                # Upgrade/Downgrade flow
                instance = request.env['saas.odoo.instance'].sudo().browse(int(instance_id))
                if not instance.exists() or instance.partner_id.id != partner.id:
                    return self._json_response(error="Instance not found or unauthorized.")
                checkout_vals.update({
                    'instance_id': instance.id,
                    'sub_domain': instance.name,
                    'domain_id': instance.based_domain_id.id,
                })
            else:
                # New deployment
                sub_domain = kwargs.get('sub_domain')
                base_domain_id = kwargs.get('base_domain_id')
                creation_mode = kwargs.get('creation_mode', 'scratch')
                template_instance_id = kwargs.get('template_instance_id')

                if not sub_domain:
                    return self._json_response(error="sub_domain is required for new subscriptions.")
                if not base_domain_id:
                    return self._json_response(error="base_domain_id is required for new subscriptions.")

                sub_domain = sub_domain.strip()
                if not sub_domain.replace('-', '').isalnum():
                    return self._json_response(error="Subdomain can only contain letters, numbers, and hyphens.")

                base_domain = request.env['saas.based.domain'].sudo().browse(int(base_domain_id))
                if not base_domain.exists():
                    return self._json_response(error="Base domain not found.")

                # Check domain availability
                full_domain = f"{sub_domain}.{base_domain.name}"
                existing = request.env['saas.odoo.instance'].sudo().search([
                    ('domain_name', '=', full_domain)
                ], limit=1)
                if existing:
                    return self._json_response(error=f"Domain {full_domain} is already taken.")

                checkout_vals.update({
                    'sub_domain': sub_domain,
                    'domain_id': base_domain.id,
                    'creation_mode': creation_mode,
                    'template_instance_id': int(template_instance_id) if template_instance_id else False,
                })

            # Create sale order
            website = request.env['website'].sudo().get_current_website() or request.env['website'].sudo().search([], limit=1)
            order = website.create_saas_order(checkout_vals)
            
            return self._json_response(data={
                'order_id': order.id,
                'order_name': order.name,
                'amount_total': order.amount_total,
                'state': order.state,
                'instance_id': order.instance_id.id if order.instance_id else False,
            })

        except AccessDenied as e:
            return self._json_response(error=str(e))
        except Exception as e:
            _logger.exception("SaaS subscribe API error")
            return self._json_response(error=str(e))
