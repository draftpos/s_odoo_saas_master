from odoo import fields, models, api


class OdooVersion(models.Model):
    _name = 'saas.odoo.version'
    _description = "SaaS Odoo Version"
    _order = 'sequence'

    name = fields.Char(string="Odoo Version", required=True)
    version = fields.Integer(string='Odoo Version (Integer)', compute='_compute_version', store=True)
    sequence = fields.Integer('Sequence', default=10, required=True)
    config_ids = fields.One2many('saas.odoo.version.config', 'odoo_version_id', string='Configs')
    active = fields.Boolean(string="Active", default=True)

    @api.depends('name')
    def _compute_version(self):
        for r in self:
            r.version = 0
            val = r.name
            if val:
                try:
                    r.version = int(val.split(".")[0])
                except (ValueError, IndexError):
                    pass

    def action_view_odoo_version_config(self):
        action = self.env['ir.actions.act_window']._for_xml_id('s_odoo_saas_master.saas_odoo_version_config_action')
        action['context'] = {'default_odoo_version_id': self.id}
        action['domain'] = [('odoo_version_id', '=', self.id)]
        return action
