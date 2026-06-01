# -*- coding: utf-8 -*-
from odoo import http
from odoo.addons.web.controllers.home import Home
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)

SAAS_MASTER_URL = 'https://saas.havano.pro'

class SaasTenantHome(Home):

    @http.route('/web/login', type='http', auth="none")
    def web_login(self, redirect=None, **kw):
        # Prevent standard login and redirect to master SaaS
        # Only allow if there's a secret bypass parameter (for emergency admin)
        if kw.get('bypass_sso'):
            return super(SaasTenantHome, self).web_login(redirect=redirect, **kw)
        
        # Determine current URL to tell master where to redirect back after login
        # (Assuming the master has a feature to redirect back to workspace, 
        # or we just send them to the master dashboard)
        master_login = f"{SAAS_MASTER_URL.rstrip('/')}/web/login"
        return request.redirect(master_login)
