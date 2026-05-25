import os
from odoo import fields, models


class OdooInstanceBackup(models.Model):
    _name = 'saas.odoo.instance.backup'
    _description = "SaaS Odoo Instance Backup"

    instance_id = fields.Many2one('saas.odoo.instance', string='Odoo Instance', required=True, ondelete='cascade')
    name = fields.Char(string="Name", required=True)
    datetime = fields.Datetime(string="Datetime")
    file_path = fields.Char(string="File Path")
    file_size = fields.Float(string="File Size (MB)")
    active = fields.Boolean(string="Active", default=True)
    description = fields.Text(string="Description")
    format = fields.Selection([
        ('zip', 'zip (includes filestore)'),
        ('dump', 'pg_dump (without filestore)')
    ], string='Backup Format', default='zip')

    def unlink(self):
        files_to_delete = []
        for rec in self:
            if rec.file_path and os.path.exists(rec.file_path):
                files_to_delete.append(rec.file_path)

        result = super(OdooInstanceBackup, self).unlink()
        for file in files_to_delete:
            os.remove(file)
        return result

    def action_download(self):
        return {
            'type': 'ir.actions.act_url',
            'target': '_blank',
            'url': '/saas_backup/download/%s' % self.id,
        }
