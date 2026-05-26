from odoo import fields, models


class PSQLVersion(models.Model):
    _name = 'saas.psql.version'
    _description = "SaaS PSQL Version"

    name = fields.Char(string="Odoo Version", required=True)
    docker_image_tag = fields.Char(string='Docker Image Tag')
    active = fields.Boolean(string="Active", default=True)
