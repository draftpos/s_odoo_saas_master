# -*- coding: utf-8 -*-
from odoo import fields, models


class SaaSPlan(models.Model):
    _name = 'saas.plan'
    _description = "SaaS Plan"
    _order = "sequence, id"

    name = fields.Char(string='Name', required=True)
    code = fields.Char(string='Code', required=True)
    description = fields.Html(string='Description')
    
    monthly_product_id = fields.Many2one(
        'product.product', 
        string='Monthly Product', 
        domain=[('sale_ok', '=', True)]
    )
    yearly_product_id = fields.Many2one(
        'product.product', 
        string='Yearly Product', 
        domain=[('sale_ok', '=', True)]
    )
    
    limit_pos_terminals = fields.Integer(string='POS Terminals Limit', default=1)
    limit_users = fields.Integer(string='Users Limit', default=5)
    
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('code_uniq', 'unique (code)', "The plan code must be unique!"),
    ]
