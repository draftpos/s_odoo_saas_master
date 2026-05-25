from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


class OdooServer(models.Model):
    _name = 'saas.odoo.server'
    _description = "SaaS Odoo Server"
    _order = 'sequence'

    name = fields.Char(string='Name', required=True)
    sequence = fields.Integer('Sequence', default=10, required=True)
    pserver_id = fields.Many2one('saas.pserver', string="Physical Server", required=True, copy=False)
    working_ip_id = fields.Many2one('saas.pserver.ip', string="Working IP", required=True, copy=False)
    odoo_version_id = fields.Many2one('saas.odoo.version', string='Odoo Version', required=True)
    psql_version_id = fields.Many2one('saas.psql.version', string='PSQL Version', required=True)
    nginx_server_id = fields.Many2one('saas.nginx.server', string='Nginx Server', required=True)
    extra_addon_ids = fields.One2many('saas.odoo.server.extra.addon', 'odoo_server_id', string='Extra Addons')
    description = fields.Text(string='Description')
    active = fields.Boolean(string="Active", default=True)

    # Non-docker Path & Config Fields
    odoo_bin_path = fields.Char(string='Odoo Binary Path', default='/usr/bin/odoo-bin', required=True)
    python_path = fields.Char(string='Python Executable Path', default='/usr/bin/python3', required=True)
    pg_host = fields.Char(string='Postgres Host', default='localhost', required=True)
    pg_user = fields.Char(string='Postgres User', default='odoo', required=True)
    pg_password = fields.Char(string='Postgres Password', default='odoo', required=True)

    # Instance limit fields
    max_instances = fields.Integer(
        string='Maximum Instances',
        default=0,
        help="Maximum number of instances allowed on this server. Set to 0 for unlimited."
    )
    instance_ids = fields.One2many(
        'saas.odoo.instance',
        'odoo_server_id',
        string='Instances',
        domain=[('state', '!=', 'cancel')]
    )
    instance_count = fields.Integer(
        string='Instance Count',
        compute='_compute_instance_count',
        store=True
    )
    available_slots = fields.Integer(
        string='Available Slots',
        compute='_compute_available_slots'
    )

    @api.depends('instance_ids', 'instance_ids.state')
    def _compute_instance_count(self):
        for server in self:
            server.instance_count = len(server.instance_ids.filtered(lambda i: i.state != 'cancel'))

    @api.depends('max_instances', 'instance_count')
    def _compute_available_slots(self):
        for server in self:
            if server.max_instances <= 0:
                server.available_slots = -1  # Unlimited
            else:
                server.available_slots = max(0, server.max_instances - server.instance_count)

    def check_instance_limit(self):
        """Check if the server can accept more instances. Raises ValidationError if limit reached."""
        self.ensure_one()
        if self.max_instances > 0 and self.instance_count >= self.max_instances:
            raise ValidationError(
                _("Server '%s' has reached its maximum instance limit (%d/%d). "
                  "Please use another server or increase the limit.")
                % (self.name, self.instance_count, self.max_instances)
            )
        return True

    def has_available_capacity(self):
        """Returns True if server can accept more instances."""
        self.ensure_one()
        if self.max_instances <= 0:
            return True
        return self.instance_count < self.max_instances
