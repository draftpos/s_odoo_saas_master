from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


class OdooInstanceConfig(models.Model):
    _name = 'saas.odoo.instance.config'
    _description = "SaaS Odoo Instance Config"

    instance_id = fields.Many2one('saas.odoo.instance', string='Odoo Instance', required=True, ondelete='cascade')
    odoo_version_id = fields.Many2one(related='instance_id.odoo_version_id')
    name = fields.Char(string='Key', required=True)
    value = fields.Char(string='Value')
    section_id = fields.Many2one('saas.odoo.version.config.section', string="Section", required=True)

    @api.model
    def _get_config_file_content(self, instance):
        file_content = ''
        sections = instance.config_ids.mapped('section_id')
        for section in sections:
            file_content += '[' + section.name + ']' + '\n'
            configs = instance.config_ids.filtered(lambda c: c.section_id == section)
            for config in configs:
                file_content += config.name + '=' + config.value + '\n'
        return file_content

    @api.model
    def _get_config_file_path(self, instance):
        return '/home/%s/config/odoo.conf' % instance.technical_name
