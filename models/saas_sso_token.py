# -*- coding: utf-8 -*-

from odoo import models, fields
import uuid
from datetime import timedelta

class SaasSsoToken(models.Model):
    _name = 'saas.sso.token'
    _description = 'SaaS SSO One-Time Token'

    token = fields.Char(string='Token', required=True, default=lambda self: uuid.uuid4().hex, index=True)
    partner_id = fields.Many2one('res.partner', string='Partner', required=True)
    user_id = fields.Many2one('res.users', string='User')
    instance_id = fields.Many2one('saas.odoo.instance', string='Instance', required=True)
    expiration_date = fields.Datetime('Expiration', required=True, default=lambda self: fields.Datetime.now() + timedelta(minutes=1))
