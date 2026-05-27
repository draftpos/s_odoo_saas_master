import random
import string
import os
import requests
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import logging
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError, UserError


_logger = logging.getLogger(__name__)


class OdooInstance(models.Model):
    _name = 'saas.odoo.instance'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _description = "SaaS Odoo Instance"

    @api.model
    def _default_based_domain(self):
        based_domain = self.env['saas.based.domain'].search([], limit=1)
        return based_domain or False

    @api.model
    def _default_odoo_version(self):
        odoo_version = self.env['saas.odoo.version'].search([], limit=1)
        return odoo_version or False

    @api.model
    def _default_backup_limit(self):
        return self.env.user.company_id.instance_backup_limit or 7

    name = fields.Char(string="Subdomain", required=True)
    url = fields.Char(string="URL", compute='_compute_url', store=True)
    technical_name = fields.Char(string="Technical Name", compute='_compute_technical_name', store=True)
    noindex = fields.Boolean(string='No Index', default=True)
    server_log = fields.Text(string="Server Logs", readonly=True, help="Latest logs from the remote server")
    domain_name = fields.Char(string="Domain Name", compute='_compute_domain_name', store=True)
    based_domain_id = fields.Many2one('saas.based.domain', string="Based Domain", required=True, default=_default_based_domain)
    odoo_version_id = fields.Many2one('saas.odoo.version', string='Odoo Version', required=True, default=_default_odoo_version)
    odoo_server_id = fields.Many2one('saas.odoo.server', string='Odoo Server', required=True,
        compute='_compute_odoo_server_id', store=True, readonly=False)
    pserver_id = fields.Many2one(related='odoo_server_id.pserver_id', store=True)
    port_ids = fields.One2many('saas.odoo.instance.port', 'instance_id', string='Odoo Instance Ports', readonly=True)
    user_demo_data = fields.Boolean(string='Use Demo Data')
    config_ids = fields.One2many('saas.odoo.instance.config', 'instance_id', string='Configs')
    db_name = fields.Char(string='Database Name', compute='_compute_db_name', store=True)
    domain_name_ids = fields.One2many('saas.odoo.instance.domain.name', 'instance_id', string='Domains Name')
    domain_name_count = fields.Integer(string="Domain Name Count", compute='_compute_domain_name_count')
    enable_autobackup = fields.Boolean(string="Enable Autobackup", default=True)
    installed_app_ids = fields.One2many('saas.odoo.instance.installed.app', 'instance_id', string='Installed Apps')
    installed_app_count = fields.Integer(string="Installed Apps Count", compute='_compute_installed_app_count')
    backup_limit = fields.Integer(string='Backup Limit', required=True, default=_default_backup_limit)
    backup_ids = fields.One2many('saas.odoo.instance.backup', 'instance_id', string='Backups')
    backup_count = fields.Integer(string="Backup Count", compute='_compute_backup_count')
    extra_addon_ids = fields.One2many('saas.odoo.instance.extra.addon', 'instance_id', string='Extra Addons')
    custom_addon_ids = fields.One2many('saas.odoo.instance.custom.addon', 'instance_id', string='Custom Addons',
        compute='_compute_custom_addon_ids', store=True, readonly=False)
    default_module = fields.Char(string='Default Modules', help="Modules are separated by commas")
    trial = fields.Boolean(string='Trial')
    expiration_date = fields.Date(string='Expiration Date', compute='_compute_expiration_date', store=True, readonly=False)
    active_user = fields.Integer(string='Active Users', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer')
    company_id = fields.Many2one('res.company', string="Company", default=lambda self: self.env.user.company_id)
    resource_package_id = fields.Many2one('saas.resource.package', string='Resource Package', compute='_compute_resource_package_id', store=True, readonly=False)
    resource_package_line_ids = fields.One2many('saas.odoo.instance.resource.package.line', 'instance_id', string='Resource Package Lines',
        compute='_compute_resource_package_line_ids', store=True, readonly=False)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('deploy', 'Deployed'),
        ('suspend', 'Suspended'),
        ('cancel', 'Cancelled'),
    ], string="Status", copy=False, index=True, readonly=True, tracking=True, default='draft')
    operation_state = fields.Selection([
        ('draft', 'Draft'),
        ('run', 'Running'),
        ('stop', 'Stopped')
    ], string='Operation Status', default='draft', readonly=True)
    is_template = fields.Boolean(string='Is Template?', help="This instance will be used as a template for other instances. "
                                 "Then the database, file store,... of this instance will be copied to the new instance as a template.")
    deploy_mail_template_id = fields.Many2one('mail.template', string='Deploy Email Template',
        help="Email template used when deploying instances created from this instance template. "
        "It is useful when the login information of this template is different from the default information. "
        "In that case, you need a separate email template for this instance template. "
        "Keep empty to use default.")
    use_template = fields.Boolean(string='Use Template', help="Use another instance's database, file store,... as a template")
    template_instance_domain_ids = fields.Many2many('saas.odoo.instance', compute='_compute_template_instance_domain_ids',
        help="Technical field used to filter domain 'template_instance_id'")
    template_instance_id = fields.Many2one('saas.odoo.instance', string='Instance Template',
        domain="[('is_template', '=', True), ('state', '=', 'deploy'), ('odoo_version_id', '=', odoo_version_id)]")

    # Removed all docker fields

    # sale
    subscription_type = fields.Selection([
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ], string='Subscription Type', readonly=True)
    sale_order_ids = fields.One2many('sale.order', 'instance_id', string='Sale Orders', readonly=True, groups="sales_team.group_sale_salesman")
    sale_order_count = fields.Integer(string='Sale Order Count', compute='_compute_sale_order_count', store=True, compute_sudo=True)
    paid_user = fields.Integer(string='Paid User', compute='_compute_paid_user', store=True)
    not_paid_app_count = fields.Integer(string="Not Paid Apps Count", compute='_compute_not_paid_app_count', store=True)
    account_move_ids = fields.One2many('account.move', 'instance_id', string='Invoices', readonly=True, groups="account.group_account_invoice")
    account_move_count = fields.Integer(string='Invoice Count', compute='_compute_account_move_count', store=True, compute_sudo=True)
    has_extra = fields.Boolean(string='Has extra addons or user', compute='_compute_has_extra', store=True)
    buy_now_from_pricing = fields.Boolean(help="Technical field")

    _sql_constraints = [
        ('name_uniq', 'unique(name,based_domain_id)', 'Subdomain must be unique per based domain!')
    ]

    @api.constrains('trial', 'partner_id', 'company_id')
    def _check_trial_instance(self):
        for r in self:
            if r.trial and r.partner_id and r.company_id:
                if r.partner_id.trial_instance_count > r.company_id.limit_trial:
                    raise ValidationError(_("Partner %s has reached the maximum number of trials. Please use the paid Odoo instance") % r.partner_id.name)

    @api.constrains('name')
    def _check_subdomain(self):
        for r in self:
            if r.name and r.name[0].isdigit():
                raise ValidationError(_("Subdomain cannot start with a number."))
            existed_domain_name = self.env['saas.odoo.instance.domain.name'].search([('name', '=', r.domain_name)], limit=1)
            if existed_domain_name:
                raise UserError(_("Subdomain %s already belongs to Odoo Instance %s") % (r.name, existed_domain_name.instance_id.name))

    @api.depends('config_ids', 'config_ids.name', 'config_ids.value')
    def _compute_db_name(self):
        for r in self:
            r.db_name = ''
            if r.config_ids:
                db_name_configs = r.config_ids.filtered(lambda c: c.name == 'db_name')
                if db_name_configs:
                    r.db_name = db_name_configs[0].value

    @api.depends('company_id')
    def _compute_resource_package_id(self):
        for r in self: 
            r.resource_package_id = False
            if r.company_id:
                r.resource_package_id = r.company_id.resource_package_id

    @api.depends('resource_package_id')
    def _compute_resource_package_line_ids(self):
        for r in self:
            r.resource_package_line_ids = False
            if r.resource_package_id:
                lines = []
                for line in r.resource_package_id.line_ids:
                    lines.append((0, 0, {
                        'name': line.name,
                        'value': line.value,
                        'type': line.type
                    }))
                r.resource_package_line_ids = lines

    @api.depends('name', 'based_domain_id.name')
    def _compute_url(self):
        for r in self:
            r.url = 'https://%s.%s' % (r.name, r.based_domain_id.name)

    @api.depends('name', 'based_domain_id')
    def _compute_domain_name(self):
        for r in self:
            domain_name = ''
            if r.name and r.based_domain_id:
                domain_name = r.name + '.' + r.based_domain_id.name
            r.domain_name = domain_name

    @api.depends('name', 'based_domain_id.name')
    def _compute_technical_name(self):
        for r in self:
            if not r.name or not r.based_domain_id:
                r.technical_name = ''
            else:
                length = 41
                technical_name = (r.name + '_' + r.based_domain_id.name).replace("-", "_").replace(".", "_") + '_'
                technical_name += self._get_technical_name(length - len(technical_name))
                r.technical_name = technical_name

    @api.depends('odoo_version_id')
    def _compute_odoo_server_id(self):
        for r in self:
            if r.odoo_version_id:
                odoo_server = self.env['saas.odoo.server'].search([('odoo_version_id', '=', r.odoo_version_id.id)], limit=1)
                r.odoo_server_id = odoo_server or False
            else:
                r.odoo_server_id = False

    @api.depends('odoo_version_id')
    def _compute_template_instance_domain_ids(self):
        for r in self:
            available_template_instances = r._get__available_template_instance()
            r.template_instance_domain_ids = [(6, 0, available_template_instances.ids)]

    def _get__available_template_instance(self):
        """
        Hook method to 's_odoo_saas_plan' module can be extended
        """
        if self.odoo_version_id:
            return self.search([('is_template', '=', True), ('state', '=', 'deploy'), ('odoo_version_id', '=', self.odoo_version_id.id)])
        else:
            return self.env['saas.odoo.instance']

    @api.depends('template_instance_id', 'name')
    def _compute_custom_addon_ids(self):
        for r in self:
            r.custom_addon_ids = False
            if r.name and r.template_instance_id:
                custom_addons = []
                for addons in r.template_instance_id.custom_addon_ids:
                    custom_addons.append((0, 0, {
                        'name': addons.name,
                        'clone_uri': addons.clone_uri,
                        'branch': addons.branch,
                    }))
                r.custom_addon_ids = custom_addons

    # Removed _compute_docker_compose, _compute_docker_container_count, _compute_docker_compose_volume_count

    @api.depends('domain_name_ids')
    def _compute_domain_name_count(self):
        domain_name_data = self.env['saas.odoo.instance.domain.name']._read_group([('instance_id', 'in', self.ids)], ['instance_id'], ['__count'])
        result = {d.id: count for d, count in domain_name_data}
        for r in self:
            r.domain_name_count = result.get(r.id, 0)

    @api.depends('backup_ids')
    def _compute_backup_count(self):
        backup_data = self.env['saas.odoo.instance.backup']._read_group([('instance_id', 'in', self.ids)], ['instance_id'], ['__count'])
        result = {d.id: count for d, count in backup_data}
        for r in self:
            r.backup_count = result.get(r.id, 0)

    @api.depends('installed_app_ids')
    def _compute_installed_app_count(self):
        app_data = self.env['saas.odoo.instance.installed.app']._read_group([('instance_id', 'in', self.ids)], ['instance_id'], ['__count'])
        result = {d.id: count for d, count in app_data}
        for r in self:
            r.installed_app_count = result.get(r.id, 0)

    @api.depends('trial')
    def _compute_expiration_date(self):
        for r in self:
            if not r.trial:
                r.expiration_date = r.expiration_date
            else:
                r.expiration_date = fields.Date.today() + timedelta(days=self.env.user.company_id.instance_trial_day)

    @api.depends('sale_order_ids')
    def _compute_sale_order_count(self):
        order_data = self.env['sale.order']._read_group([('instance_id', 'in', self.ids)], ['instance_id'], ['__count'])
        result = {d.id: count for d, count in order_data}
        for r in self:
            r.sale_order_count = result.get(r.id, 0)

    @api.depends(
        'sale_order_ids', 'sale_order_ids.state', 'sale_order_ids.order_line',
        'sale_order_ids.order_line.product_id', 'sale_order_ids.order_line.product_uom_qty')
    def _compute_paid_user(self):
        for r in self:
            order_lines = r.sale_order_ids.order_line.filtered(lambda line: line.order_id.state == 'sale' and line.product_id.is_saas_user)
            r.paid_user = sum(order_lines.mapped('product_uom_qty'))

    @api.depends('installed_app_ids')
    def _compute_not_paid_app_count(self):
        not_paid_app_data = self.env['saas.odoo.instance.installed.app']._read_group([
            ('instance_id', 'in', self.ids), ('not_paid', '=', True)], ['instance_id'], ['__count'])
        result = {d.id: count for d, count in not_paid_app_data}
        for r in self:
            r.not_paid_app_count = result.get(r.id, 0)

    @api.depends('account_move_ids')
    def _compute_account_move_count(self):
        move_data = self.env['account.move']._read_group([('instance_id', 'in', self.ids)], ['instance_id'], ['__count'])
        result = {d.id: count for d, count in move_data}
        for r in self:
            r.account_move_count = result.get(r.id, 0)

    @api.depends('not_paid_app_count', 'active_user', 'paid_user')
    def _compute_has_extra(self):
        for r in self:
            r.has_extra = False
            if r.partner_id and r.expiration_date:
                if r.paid_user and r.active_user > r.paid_user:
                    r.has_extra = True
                    continue
                if r.not_paid_app_count:
                    r.has_extra = True
                    continue

    def _compute_access_url(self):
        super(OdooInstance, self)._compute_access_url()
        for r in self:
            r.access_url = '/my/saas/odoo-instance/%s' % (r.id)

    @api.ondelete(at_uninstall=False)
    def unlink_exception(self):
        for r in self:
            if r.state not in ('draft', 'cancel'):
                raise UserError(_('You cannot delete this record which is not draft or cancelled.'))

    def action_view_odoo_instance_config(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_instance_config_action')
        action['context'] = {'default_instance_id': self.id}
        action['domain'] = [('instance_id', '=', self.id)]
        return action

    # Removed action_view_odoo_instance_container, action_view_odoo_instance_volume

    def action_view_odoo_instance_domain_name(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_instance_domain_name_action')
        action['context'] = {'default_instance_id': self.id}
        action['domain'] = [('instance_id', '=', self.id)]
        return action

    def action_view_odoo_instance_backup(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_instance_backup_action')
        action['context'] = {'default_instance_id': self.id}
        action['domain'] = [('instance_id', '=', self.id)]
        return action

    def action_view_installed_app(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_instance_installed_app_action')
        action['context'] = {'default_instance_id': self.id}
        action['domain'] = [('instance_id', '=', self.id)]
        return action

    def action_view_sale_order(self):
        action = self.env['ir.actions.act_window']._for_xml_id('sale.action_quotations_with_onboarding')
        action['context'] = {
            'default_instance_id': self.id,
            'default_partner_id': self.partner_id.id or False,
        }
        action['domain'] = [('instance_id', '=', self.id)]
        return action

    def action_view_account_move(self):
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_out_invoice_type')
        action['context'] = {
            'default_instance_id': self.id,
            'default_partner_id': self.partner_id.id or False,
            'default_move_type': 'out_invoice'}
        action['domain'] = [('instance_id', '=', self.id)]
        return action

    def action_open_redeploy_wizard(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_instance_redeploy_wizard_action')
        action['context'] = {'default_instance_id': self.id}
        return action

    def action_open_upgrade_module_wizard(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_instance_upgrade_module_wizard_action')
        action['context'] = {'default_instance_id': self.id}
        return action

    def action_deploy(self):
        for r in self:
            # Check server instance limit before deploying
            if r.odoo_server_id:
                r.odoo_server_id.check_instance_limit()
            if r.use_template and r.template_instance_id and r.template_instance_id.state != 'deploy':
                raise ValidationError(_("Template instance %s has not deployed yet. Please deploy it first."))
            r._generate_instance_port()
            r._generate_instance_config()
            r._generate_instance_extra_addons()
            r._generate_instance_domain_name()
            if r.use_template and r.template_instance_id:
                r.pserver_id._deploy_odoo_instance_from_template(r)
            else:
                r.pserver_id._deploy_odoo_instance(r)
            if r.partner_id and r.partner_id.email:
                if r.use_template and r.template_instance_id and r.template_instance_id.deploy_mail_template_id:
                    r.template_instance_id.deploy_mail_template_id.sudo().send_mail(r.id, force_send=True)
                else:
                    template_id = self.env.ref('s_odoo_saas_master.deploy_instance_mail_template', raise_if_not_found=False)
                    if template_id:
                        template_id.sudo().send_mail(r.id, force_send=True)

        self.write({'state': 'deploy', 'operation_state': 'run', 'buy_now_from_pricing': False})
        self.domain_name_ids.write({'state': 'deploy'})
        self.custom_addon_ids.write({'cloned': True})
        for r in self:
            if r.template_instance_id:
                r.action_get_active_users()
                r.action_get_installed_apps()

    def _action_cancel(self):
        for r in self:
            r._free_instance_port()
            r.config_ids.unlink()
            r.pserver_id._revoke_odoo_instance(r)
            if r.partner_id and r.partner_id.email:
                template_id = self.env.ref('s_odoo_saas_master.cancel_instance_mail_template', raise_if_not_found=False)
                if template_id:
                    template_id.sudo().send_mail(r.id, force_send=True)

        self.write({'state': 'cancel', 'operation_state': 'draft'})
        self.domain_name_ids.write({'state': 'cancel'})
        self.custom_addon_ids.write({'cloned': False})
    
    def action_cancel(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_instance_cancel_wizard_action')
        action['context'] = {'default_instance_id': self.id}
        return action

    def action_draft(self):
        self.write({'state': 'draft', 'operation_state': 'draft'})
        self.domain_name_ids.write({'state': 'draft'})

    def action_backup(self):
        for r in self:
            url = r.url + '/web/database/backup'
            # url = 'http://%s/web/database/backup' % r.domain_name
            try:
                filename = '%s_%s.zip' % (r.db_name, datetime.now().strftime('%Y%m%d_%H%M%S'))
                cookies = {'session_id': self.env['ir.config_parameter'].sudo().get_param('web.session.cookie_id_saas', '')}
                master_pwd = self.env['ir.config_parameter'].sudo().get_param('database.master_pwd_saas', 'admin')
                data = {
                    'master_pwd': master_pwd,
                    'name': r.db_name,
                    'backup_format': 'zip',
                }
                resp = requests.post(url, data=data, cookies=cookies, verify=False)

                # Get backup directory from company settings or fallback to system parameter
                backup_dir = self.company_id.backup_directory or self.env['ir.config_parameter'].sudo().get_param('saas.backup_directory', '/var/lib/odoo/backups')
                
                # Create backup directory if it doesn't exist
                if not os.path.isdir(backup_dir):
                    try:
                        os.makedirs(backup_dir, 0o755, exist_ok=True)
                    except (OSError, IOError) as e:
                        raise UserError(_(
                            f"Permission denied: Cannot create directory '{backup_dir}'. "
                            f"Please configure a valid backup directory in Settings → Technical → System Parameters → saas.backup_directory"
                        ))
                
                # Write the backup file
                filepath = os.path.join(backup_dir, filename)
                backup_vals = {
                    'instance_id': r.id,
                    'name': filename,
                    'datetime': fields.Datetime.now(),
                    'file_path': filepath,
                    'format': 'zip',
                }

                with open(filepath, 'wb') as f:
                    f.write(resp.content)

                filesize = round(os.path.getsize(filepath) / 1024 / 1024, 2)  # In MB
                backup_vals['file_size'] = filesize
                self.env['saas.odoo.instance.backup'].sudo().create(backup_vals)
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Backup'),
                        'message': _('Backup has been created successfully!'),
                        'sticky': False,
                    }
                }
            except Exception as e:
                error = str(e) or repr(e)
                _logger.error("Backup Instance Error: %s" % error)
                raise UserError(_("Backup Instance Error: %s") % error)

    def action_restart(self):
        for r in self:
            r.pserver_id._systemd_operation(r, 'restart')

    def action_stop(self):
        for r in self:
            r.pserver_id._systemd_operation(r, 'stop')
        self.write({'operation_state': 'stop'})

    def action_start(self):
        for r in self:
            r.pserver_id._systemd_operation(r, 'start')
        self.write({'state': 'deploy', 'operation_state': 'run'})

    def action_suspend(self, has_extra=False):
        self.action_stop()
        self.write({'state': 'suspend', 'operation_state': 'stop'})
        for r in self:
            if r.partner_id and r.partner_id.email:
                if not has_extra:
                    template_id = self.env.ref('s_odoo_saas_master.suspend_instance_mail_template', raise_if_not_found=False)
                else:
                    template_id = self.env.ref('s_odoo_saas_master.suspend_instance_with_extra_mail_template', raise_if_not_found=False)
                if template_id:
                    template_id.sudo().send_mail(r.id, force_send=True)

    def action_redeploy_config(self):
        for r in self:
            r.pserver_id._redeploy_odoo_instance_config(r)

    def action_redeploy_nginx(self):
        for r in self:
            r.pserver_id._redeploy_odoo_instance_nginx(r.domain_name_ids)

    def action_upgrade_modules(self, module):
        for r in self:
            odoo_command = 'odoo -u %s -d %s' % (module, r.technical_name)
            r.pserver_id._recreate_docker_compose_file(r, odoo_command)

    def action_install_modules(self, module):
        for r in self:
            odoo_command = 'odoo -i %s -d %s' % (module, r.technical_name)
            r.pserver_id._recreate_docker_compose_file(r, odoo_command)

    @api.model
    def _get_technical_name(self, length):
        return ''.join(random.choice(string.ascii_lowercase) for i in range(length))

    # Removed _prepare_docker_containers, _prepare_docker_compose_odoo_volumes

    def _generate_instance_port(self):
        starting_port = self.env.user.company_id.instance_starting_port

        free_ports = self.env['saas.odoo.instance.port'].search([('instance_id', '=', False)], order='port', limit=3)
        if free_ports:
            return free_ports.sudo().write({'instance_id': self.id})

        used_port = self.env['saas.odoo.instance.port'].search([('instance_id', '!=', False)], order='port desc', limit=1)
        if used_port:
            starting_port = used_port.port + 1

        port_vals_list = [
            {'name': 'xmlrpc_port', 'port': starting_port, 'pserver_id': self.pserver_id.id, 'instance_id': self.id},
            {'name': 'xmlrpcs_port', 'port': starting_port + 1, 'pserver_id': self.pserver_id.id, 'instance_id': self.id},
            {'name': 'longpolling_port', 'port': starting_port + 2, 'pserver_id': self.pserver_id.id, 'instance_id': self.id},
        ]
        return self.env['saas.odoo.instance.port'].sudo().create(port_vals_list)

    def _generate_instance_config(self):        
        conf_vals_list = self._prepare_conf_vals_list()
        self.config_ids.unlink()
        return self.env['saas.odoo.instance.config'].create(conf_vals_list)
    
    def _prepare_conf_vals_list(self):
        conf_vals_list = []
        handled_configs = set()
        for conf in self.odoo_version_id.config_ids:
            value = conf.value
            if conf.name == 'addons_path':
                addons_path = []
                if not self.odoo_server_id.extra_addon_ids and not self.custom_addon_ids:
                    addons_path = ['/home/%s/custom-addons' % self.technical_name]
                else:
                    if self.odoo_server_id.extra_addon_ids:
                        addons_path += self.odoo_server_id.extra_addon_ids.mapped('source_path')
                    if self.custom_addon_ids:
                        addons_path += self.custom_addon_ids.mapped('addon_path')
                value = ','.join(addons_path)
            elif conf.name == 'admin_passwd':
                value = ''.join(random.choice(string.ascii_lowercase) for i in range(32))
            elif conf.name == 'data_dir':
                value = '/home/%s/odoo-web-data' % self.technical_name
            elif conf.name == 'db_name':
                value = self.technical_name
            elif conf.name == 'dbfilter':
                value = '^%s$' % self.technical_name
            elif conf.name == 'logfile':
                value = '/home/%s/odoo.log' % self.technical_name
            elif conf.name == 'without_demo':
                value = not self.user_demo_data
            elif conf.name in ['xmlrpc_port', 'xmlrpcs_port', 'longpolling_port', 'gevent_port']:
                port_name = 'longpolling_port' if conf.name == 'gevent_port' else conf.name
                port_record = self.port_ids.filtered(lambda p: p.name == port_name)
                if port_record:
                    value = port_record[0].port

            conf_vals_list.append({
                'instance_id': self.id,
                'name': conf.name,
                'value': str(value),
                'section_id': conf.section_id.id,
            })
            handled_configs.add(conf.name)

        # Inject host PostgreSQL credentials dynamically if not already defined
        section = self.env['saas.odoo.version.config.section'].search([], limit=1)
        if section:
            db_configs = {
                'db_host': self.odoo_server_id.pg_host or 'localhost',
                'db_user': self.odoo_server_id.pg_user or 'odoo',
                'db_password': self.odoo_server_id.pg_password or 'odoo',
            }
            for name, value in db_configs.items():
                if name not in handled_configs:
                    conf_vals_list.append({
                        'instance_id': self.id,
                        'name': name,
                        'value': str(value),
                        'section_id': section.id,
                    })
                    handled_configs.add(name)

        return conf_vals_list

    def _generate_instance_extra_addons(self):
        extra_addon_vals_list = []
        self.extra_addon_ids.sudo().unlink()
        if self.use_template and self.template_instance_id: 
            for extra_addon in self.template_instance_id.extra_addon_ids:
                extra_addon_vals_list.append({
                    'instance_id': self.id,
                    'name': extra_addon.name,
                    'addon_path': extra_addon.addon_path,
                    'copy_to': extra_addon.copy_to,
                    'container_path': extra_addon.container_path,
                })
        else:
            for extra_addon in self.odoo_server_id.extra_addon_ids:
                extra_addon_vals_list.append({
                    'instance_id': self.id,
                    'name': extra_addon.source_path.split('/')[-1],
                    'addon_path': extra_addon.source_path,
                    'copy_to': '/home/%s/custom-addons' % self.technical_name,
                    'container_path': extra_addon.docker_container_path,
                })

        self.env['saas.odoo.instance.extra.addon'].sudo().create(extra_addon_vals_list)

    def action_fetch_logs(self):
        for r in self:
            if not r.pserver_id:
                raise UserError(_("No physical server linked to this instance."))
            ssh = r.pserver_id._connect()
            try:
                cmd = f"tail -n 150 /home/{r.technical_name}/odoo.log"
                stdin, stdout, stderr = ssh.exec_command(cmd)
                logs = stdout.read().decode('utf-8')
                r.server_log = logs
            except Exception as e:
                r.server_log = f"Failed to fetch logs: {e}"
            finally:
                ssh.close()

    def _generate_instance_domain_name(self):
        domain_name = self.env['saas.odoo.instance.domain.name'].search([('name', '=', self.domain_name)])
        if not domain_name:
            domain_name = self.env['saas.odoo.instance.domain.name'].create({
                'name': self.domain_name,
                'instance_id': self.id,
                'is_instance_domain_name': True,
                'noindex': self.noindex,
            })
        else:
            domain_name.write({'noindex': self.noindex})
        return domain_name

    def _free_instance_port(self):
        self.port_ids.sudo().write({'instance_id': False})

    def _get_domain_name(self):
        return self.name + '.' + self.based_domain_id.name

    # Removed _get_docker_compose_file_content and _get_docker_compose_file_path

    def _get_systemd_service_file_content(self):
        server = self.odoo_server_id
        content = f"""[Unit]
Description=Odoo Instance {self.technical_name}
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/{self.technical_name}
ExecStart={server.python_path} {server.odoo_bin_path} -c /home/{self.technical_name}/config/odoo.conf
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        return content

    def _get_systemd_service_file_path(self):
        return f"/etc/systemd/system/odoo-{self.technical_name}.service"

    @api.model
    def cron_auto_backup(self):
        for instance in self.search([('enable_autobackup', '=', True), ('state', '=', 'deploy')]):
            try:
                instance.action_backup()
            except Exception as e:
                error = "Error when creating backup for %s: %s" % (instance.name, str(e) or repr(e))
                _logger.exception(error)

    def _get_except_date_remove_backup(self):
        # Số lần giữ bản backup quá ngày:
        num_except_date = 4
        prior_week_end = date.today() - timedelta(days=((date.today().isoweekday() + 1) % 7))
        # prior_week_start = prior_week_end - timedelta(days=6)
        result = []
        for i in range(num_except_date):
            result.append(prior_week_end - timedelta(days=((i + 1) * 6)))
        return result

    @api.model
    def cron_clean_backup(self):
        backups_to_unlink = self.env['saas.odoo.instance.backup']
        except_date = self._get_except_date_remove_backup()
        instances = self.search([]).filtered(lambda i: i.backup_limit and len(i.backup_ids) > i.backup_limit)
        for ins in instances:
            backups = ins.backup_ids.sorted(key='datetime', reverse=True).filtered(lambda b: b.datetime.date() not in except_date)
            backups_to_unlink |= backups[ins.backup_limit:]
        if backups_to_unlink:
            backups_to_unlink.unlink()

    def action_get_active_users(self):
        results = {}
        instances = self.filtered(lambda i: i.state == 'deploy')
        for pserver in self.pserver_id:
            results.update(pserver._get_active_user(instances))
        for r in self:
            active_user = results.get(r.id, 0)
            r.write({'active_user': active_user})

    def action_get_installed_apps(self):
        results = {}
        instances = self.filtered(lambda i: i.state == 'deploy')
        for pserver in self.pserver_id:
            results.update(pserver._get_installed_apps(instances))
        for r in self:
            r.installed_app_ids.sudo().unlink()
            installed_apps = results.get(r.id, [])
            installed_app_ids = []
            for app in installed_apps:
                app_product = self.env['product.product'].search([('technical_name', '=', app['technical_name'])], limit=1)
                installed_app_ids.append((0, 0, {
                    'name': app['name'],
                    'technical_name': app['technical_name'],
                    'installed_date': app['installed_date'],
                    'product_id': app_product.id or False
                }))
            r.sudo().write({'installed_app_ids': installed_app_ids})

    def _notify_expiration(self):
        for r in self:
            if r.expiration_date and r.state == 'deploy':
                delta_days = (r.expiration_date - fields.Date.today()).days
                if delta_days <= r.company_id.notification_expiration_day:
                    if r.partner_id:
                        order = r._create_renew_so()
                        order.action_confirm()
                        invoice = order._create_saas_invoice()
                        invoice._post()
                        if r.partner_id.email:
                            template_id = self.env.ref('s_odoo_saas_master.instance_expiration_notify_mail_template', raise_if_not_found=False)
                            if template_id:
                                template_id.sudo().send_mail(r.id, force_send=True)

    def _notify_revoke(self):
        for r in self:
            if r.expiration_date:
                delta_days = (r.expiration_date - fields.Date.today()).days
                if delta_days <= r.company_id.revoke_instance_day:
                    if r.partner_id and r.partner_id.email:
                        template_id = self.env.ref('s_odoo_saas_master.instance_revoke_notify_mail_template', raise_if_not_found=False)
                        if template_id:
                            template_id.sudo().send_mail(r.id, force_send=True)

    @api.model
    def cron_get_active_user(self):
        instances = self.search([('state', '=', 'deploy')])
        instances.action_get_active_users()

    @api.model
    def cron_get_installed_app(self):
        instances = self.search([('state', '=', 'deploy')])
        instances.action_get_installed_apps()

    @api.model
    def cron_expiration_notification(self):
        instances = self.search([('state', '=', 'deploy')])
        instances._notify_expiration()

    @api.model
    def cron_suspend_instance(self):
        instances = self.search([('state', '=', 'deploy')])
        for instance in instances:
            if instance.expiration_date:
                if instance.expiration_date < fields.Date.today():
                    instance.action_suspend()
                else:
                    if instance.partner_id and instance.sale_order_ids:
                        if not instance.account_move_ids or any(move.payment_state != 'paid' for move in instance.account_move_ids):
                            instance.action_suspend(has_extra=True)

    @api.model
    def cron_create_extra_so(self):
        instances = self.search([('state', '=', 'deploy'), ('has_extra', '=', True)])
        for instance in instances:
            order = instance._create_renew_so(buy_extra=True)
            order.action_confirm()
            invoice = order._create_saas_invoice()
            invoice._post()

    @api.model
    def cron_revoke_notification(self):
        instances = self.search([('state', '=', 'deploy')])
        instances._notify_revoke()

    @api.model
    def cron_revoke_instance(self):
        instances = self.search([('state', 'in', ('deploy', 'suspend'))])
        for instance in instances:
            if instance.expiration_date:
                delta_days = (fields.Date.today() - instance.expiration_date).days
                if delta_days > instance.company_id.revoke_instance_day:
                    instance._action_cancel()

    @api.model
    def _prepare_instance_val_to_create(self, data):
        based_domain = data.get('based_domain')
        default_modules = data.get('default_modules')
        subscription_type = data.get('subscription_type')
        trial = data.get('trial')
        sub_domain = data.get('sub_domain')
        partner = data.get('partner')
        buy_now_from_pricing = data.get('buy_now_from_pricing', False)

        odoo_version = self._default_odoo_version()
        if not odoo_version:
            raise ValidationError(_("Cannot find Odoo version"))
        odoo_server = self.env['saas.odoo.server'].search([('odoo_version_id', '=', odoo_version.id)], limit=1)
        if not odoo_server:
            raise ValidationError(_("Cannot find Odoo server of Odoo version % s") % odoo_version.name)
        if not based_domain:
            based_domain = self._default_based_domain()
        if not based_domain:
            raise ValidationError(_("Cannot find Based Domain"))

        default_module = self._get_default_modules(default_modules)
        expiration_date = self._get_expiration_date(subscription_type, trial=trial)
        backup_limit = self._default_backup_limit()

        res = {
            'name': sub_domain,
            'based_domain_id': based_domain.id,
            'partner_id': partner.id or False,
            'default_module': default_module,
            'odoo_version_id': odoo_version.id,
            'odoo_server_id': odoo_server.id,
            'backup_limit': backup_limit,
            'trial': trial,
            'subscription_type': subscription_type,
            'expiration_date': expiration_date,
            'buy_now_from_pricing': buy_now_from_pricing,
        }
        return res

    @api.model
    def _get_default_modules(self, default_modules):
        if not default_modules:
            return ''
        return ','.join(default_modules)

    @api.model
    def _get_expiration_date(self, subscription_type, trial=False, expiration_date=False):
        today = fields.Date.today()
        if trial:
            expiration_date = today + timedelta(days=self.env.user.company_id.instance_trial_day)
            return expiration_date
        if subscription_type == 'monthly':
            if not expiration_date:
                expiration_date = today + relativedelta(months=1)
            else:
                expiration_date = expiration_date + relativedelta(months=1)
        elif subscription_type == 'yearly':
            if not expiration_date:
                expiration_date = today + relativedelta(months=12)
            else:
                expiration_date = expiration_date + relativedelta(months=12)
        return expiration_date

    def action_renew(self):
        order = self._create_renew_so()
        if not order:
            return
        return {
            'name': _('Quotation'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'res_model': 'sale.order',
            'res_id': order.id
        }

    def action_buy_extra(self):
        order = self._create_renew_so(buy_extra=True)
        if not order:
            return
        return {
            'name': _('Quotation'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'res_model': 'sale.order',
            'res_id': order.id
        }

    def _create_renew_so(self, buy_extra=False):
        if not self.partner_id:
            return False

        vals = self._prepare_renew_so_vals(buy_extra=buy_extra)        
        return self.env['sale.order'].create(vals)

    def _prepare_renew_so_vals(self, buy_extra):
        order_lines = []

        product_user = self.env['product.product']
        if not buy_extra:
            product_user = self.env.ref('s_odoo_saas_master.product_saas_user')
        elif (self.active_user - self.paid_user) > 0:
            product_user = self.env.ref('s_odoo_saas_master.product_saas_user')
        if product_user:
            order_lines.append((0, 0, {
                'product_id': product_user.id,
                'product_uom': product_user.uom_id.id,
                'price_unit': product_user.list_price,
                'product_uom_qty': max(self.active_user, self.paid_user) if not buy_extra else (self.active_user - self.paid_user),
            }))

        apps = self.env['saas.odoo.instance.installed.app']
        if not buy_extra:
            apps = self.installed_app_ids
        else:
            apps = self.installed_app_ids.filtered(lambda a: a.not_paid)
        for app in apps:
            product_app = self.env['product.product'].search([('technical_name', '=', app.technical_name)], limit=1)
            if product_app:
                order_lines.append((0, 0, {
                    'product_id': product_app.id,
                    'product_uom': product_app.uom_id.id,
                    'price_unit': product_app.list_price,
                    'product_uom_qty': 12 if self.subscription_type == 'yearly' else 1,
                }))

        if not order_lines:
            return False
        vals = {
            'partner_id': self.partner_id.id,
            'is_saas_order': True,
            'subscription_type': self.subscription_type,
            'instance_id': self.id,
            'saas_order_type': 'renew' if not buy_extra else 'buy_extra',
            'order_line': order_lines
        }
        return vals

    def action_update_resource_package(self):
        self.pserver_id._recreate_docker_compose_file(self, update=True)
