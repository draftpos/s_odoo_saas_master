from odoo import fields, models


class PSQLVersion(models.Model):
    _name = 'saas.psql.version'
    _description = "SaaS PSQL Version"

    name = fields.Char(string="Odoo Version", required=True)
    active = fields.Boolean(string="Active", default=True)
