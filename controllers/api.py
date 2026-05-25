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
            name = kwargs.get('name')
            email = kwargs.get('email')
            password = kwargs.get('password')
            phone = kwargs.get('phone')

            if not name or not email or not password:
                return self._json_response(error="Name, email, and password are required.")

            # Check if user exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing_user:
                return self._json_response(error="A user with this email already exists.")

            # Create user
            user_vals = {
                'name': name,
                'login': email,
                'email': email,
                'password': password,
            }
            if phone:
                user_vals['phone'] = phone

            user = request.env['res.users'].sudo().create(user_vals)

            # Generate API token
            token = request.env['saas.api.token'].sudo().create({
                'partner_id': user.partner_id.id,
                'name': 'Mobile App Token',
            })

            return self._json_response(data={
                'user_id': user.id,
                'partner_id': user.partner_id.id,
                'api_key': token.token,
                'message': 'Registration successful',
            })

        except Exception as e:
            _logger.exception("Registration error")
            return self._json_response(error=str(e))

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

            if not email or not password:
                return self._json_response(error="Email and password are required.")

            # Authenticate
            try:
                uid = request.session.authenticate(request.db, email, password)
            except Exception:
                return self._json_response(error="Invalid email or password.")

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

            return self._json_response(data={
                'user_id': user.id,
                'partner_id': user.partner_id.id,
                'api_key': token.token,
                'name': user.name,
                'email': user.email,
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

    @http.route('/api/v1/instances', type='json', auth='public', methods=['GET', 'POST'], csrf=False)
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

            if request.httprequest.method == 'GET':
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

                if not subdomain:
                    return self._json_response(error="Subdomain is required.")

                if not base_domain_id:
                    return self._json_response(error="Base domain ID is required.")

                # Validate subdomain
                if subdomain[0].isdigit():
                    return self._json_response(error="Subdomain cannot start with a number.")

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

                # Prepare instance values
                instance_vals = {
                    'name': subdomain,
                    'based_domain_id': base_domain_id,
                    'odoo_server_id': odoo_server.id,
                    'partner_id': partner.id,
                    'trial': is_trial,
                }

                if apps:
                    instance_vals['default_module'] = ','.join(apps)

                if is_trial:
                    trial_days = partner.company_id.instance_trial_day or 15
                    instance_vals['expiration_date'] = fields.Date.today() + timedelta(days=trial_days)

                # Create instance
                instance = request.env['saas.odoo.instance'].sudo().create(instance_vals)

                # Auto-deploy
                try:
                    instance.action_deploy()
                except Exception as e:
                    _logger.exception("Instance deployment error")
                    return self._json_response(error=f"Instance created but deployment failed: {str(e)}")

                return self._json_response(data={
                    'instance': self._get_instance_data(instance),
                    'message': 'Instance created and deployed successfully',
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
