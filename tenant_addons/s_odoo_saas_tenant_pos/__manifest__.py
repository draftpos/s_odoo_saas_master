# -*- coding: utf-8 -*-
{
    'name': 'SaaS Tenant POS Extension',
    'version': '19.0.1.0',
    'category': 'Hidden',
    'summary': 'Enforces POS terminal limit checks on SaaS tenant databases',
    'description': """
Enforces POS terminal limit checks on SaaS tenant databases based on plan limits.
    """,
    'depends': ['s_odoo_saas_tenant', 'point_of_sale'],
    'data': [],
    'installable': True,
    'auto_install': True,
}
