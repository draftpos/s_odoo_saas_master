from collections import OrderedDict
import logging
from odoo import http, _
from odoo.osv import expression
from odoo.exceptions import MissingError
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.http import request, content_disposition

_logger = logging.getLogger(__name__)


class PortalInstance(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'instance_count' in counters:
            values['instance_count'] = request.env.user.partner_id.instance_count
        return values

    def _get_instance_searchbar_sortings(self):
        return {
            'date': {'label': _('Expiration Date'), 'order': 'expiration_date desc'},
            'state': {'label': _('Status'), 'order': 'state'},
        }

    def _get_instance_searchbar_filters(self):
        return {
            'all': {'label': _('All'), 'domain': []},
            'deploy': {'label': _('Deployed'), 'domain': [('state', '=', 'deploy')]},
            'suspend': {'label': _('Suspended'), 'domain': [('state', '=', 'suspend')]},
            'cancel': {'label': _('Cancelled'), 'domain': [('state', '=', 'cancel')]},
        }

    def _get_instances_domain(self):
        return [('partner_id', '=', request.env.user.partner_id.id)]

    def _validate_instance(self, instance):
        if request.env.user.partner_id.id != instance.partner_id.id:
            return request.redirect('/my')

    @http.route(['/my/saas/odoo-instances'], type='http', auth="user", website=True)
    def portal_my_instances(self, sortby=None, filterby=None, **kw):
        values = self._prepare_my_instances_values(sortby, filterby)
        return request.render("s_odoo_saas_master.portal_my_instances", values)

    @http.route(['/my/saas/odoo-instance/<int:instance_id>'], type='http', auth="public", website=True)
    def portal_my_instance_detail(self, instance_id, access_token=None, **kw):
        values = self._instance_get_page_view_values(instance_id, access_token, **kw)
        return request.render("s_odoo_saas_master.portal_instance_page", values)

    def _prepare_my_instances_values(self, sortby, filterby, domain=None, url="/my/saas/odoo-instances"):
        values = self._prepare_portal_layout_values()
        domain = expression.AND([
            domain or [],
            self._get_instances_domain(),
        ])

        searchbar_sortings = self._get_instance_searchbar_sortings()
        if not sortby:
            sortby = 'date'
        order = searchbar_sortings[sortby]['order']

        searchbar_filters = self._get_instance_searchbar_filters()
        if not filterby:
            filterby = 'all'
        domain += searchbar_filters[filterby]['domain']

        instances = request.env['saas.odoo.instance'].sudo().search(domain, order=order)

        values.update({
            'instances': instances,
            'page_name': 'instance',
            'default_url': url,
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
            'searchbar_filters': OrderedDict(sorted(searchbar_filters.items())),
            'filterby': filterby,
        })

        return values

    def _instance_get_page_view_values(self, instance_id, access_token, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        self._validate_instance(instance)
        values = {
            'page_name': 'instance',
            'instance': instance,
            'installed_apps': instance.installed_app_ids,
            'managing_ip': instance.pserver_id._get_managing_ip()
        }
        return self._get_page_view_values(instance, access_token, values, 'my_instances_history', False, **kwargs)

    @http.route('/saas/instance/stop', type='json', auth='user')
    def instance_stop(self, instance_id, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        if instance.state != 'deploy':
            return False
        self._validate_instance(instance)
        instance.action_stop()
        return True

    @http.route('/saas/instance/deploy', type='json', auth='user')
    def instance_deploy(self, instance_id, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        if instance.state != 'draft' and not instance.buy_now_from_pricing:
            return False
        self._validate_instance(instance)
        instance.action_deploy()
        return True
    
    @http.route('/saas/instance/start', type='json', auth='user')
    def instance_start(self, instance_id, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        if instance.state != 'deploy':
            return False
        self._validate_instance(instance)
        instance.action_start()
        return True

    @http.route('/saas/instance/restart', type='json', auth='user')
    def instance_restart(self, instance_id, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        if instance.state != 'deploy':
            return False
        self._validate_instance(instance)
        instance.action_restart()
        return True

    @http.route('/saas/instance/create-backup', type='json', auth='user')
    def instance_create_backup(self, instance_id, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        self._validate_instance(instance)
        instance.action_backup()
        return True

    @http.route([
        '/my/instance/<int:instance_id>/download-backup',
        '/my/instance/<int:instance_id>/download-backup/<int:backup_id>'
    ], type='http', auth='user')
    def instance_download_backup(self, instance_id, backup_id=None, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        self._validate_instance(instance)
        backup = False
        if not backup_id:
            backup = instance.backup_ids.sorted('datetime', reverse=True)[:1]
        else:
            backup = request.env['saas.odoo.instance.backup'].sudo().browse(backup_id) & instance.backup_ids
        if not backup:
            raise MissingError(_("Backup does not exist."))
        try:
            headers = [
                ('Content-Type', 'application/octet-stream; charset=binary'),
                ('Content-Disposition', content_disposition(backup.name)),
            ]
            with open(backup.file_path, mode='rb') as f:
                stream = f.read()
            response = request.make_response(stream, headers=headers)
            return response
        except Exception as e:
            error = "Download backup error: %s" % (str(e) or repr(e))
            _logger.exception(error)
            return self.portal_my_instance_detail(instance_id)

    @http.route(['/saas/instance/check-domain-name'], type='json', auth='user')
    def instance_check_domain_name(self, domain_name):
        instance_domain_name = request.env['saas.odoo.instance.domain.name'].sudo().search([
            ('name', '=', domain_name),
        ], limit=1)
        if instance_domain_name:
            error = _("Your domain name has already been taken. Please choose another one.")
            return {
                'success': False,
                'error': error,
            }
        return {'success': True}

    @http.route('/saas/instance/remove-domain-name', type='json', auth='user')
    def instance_remove_domain_name(self, domain_name_id, **kwargs):
        domain_name = request.env['saas.odoo.instance.domain.name'].sudo().browse(domain_name_id)
        self._validate_instance(domain_name.instance_id)
        domain_name.action_cancel()
        domain_name.unlink()
        return True

    @http.route('/saas/instance/add-domain-name', type='json', auth='user')
    def instance_add_domain_name(self, instance_id, domain_name, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        self._validate_instance(instance)
        instance_domain_name = request.env['saas.odoo.instance.domain.name'].sudo().create({
            'instance_id': instance_id,
            'name': domain_name,
        })
        instance_domain_name.action_deploy()
        return True

    @http.route('/saas/instance/get-app-and-user', type='json', auth='user')
    def instance_get_app_and_user(self, instance_id, **kwargs):
        instance = request.env['saas.odoo.instance'].sudo().browse(instance_id)
        if instance.state != 'deploy':
            return False
        self._validate_instance(instance)
        instance.action_get_active_users()
        instance.action_get_installed_apps()
        return True
