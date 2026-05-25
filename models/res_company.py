from odoo import fields, models, api


class Company(models.Model):
    _inherit = 'res.company'

    instance_starting_port = fields.Integer(string='Default Instance Starting Port', required=True, default=9000,
        help="Default starting port of odoo instance when create physical server")
    instance_backup_limit = fields.Integer(string='Default Instance Backup Limit', required=True, default=7,
        help="Set to zero to be no limit.")
    backup_directory = fields.Char(string='Backup Directory', default='/var/lib/odoo/backups',
        help="Directory where backups will be stored. Should be accessible by Odoo process.")
    instance_trial_day = fields.Integer(string='Default Trial Day', required=True, default=15)
    notification_expiration_day = fields.Integer(string='Notification Expiration Day', required=True, default=5)
    revoke_instance_day = fields.Integer(string='Revoke Odoo Instance Day', required=True, default=15)
    limit_trial = fields.Integer(string='Maximum Trial per Customer', required=True, default=1)
    resource_package_id = fields.Many2one('saas.resource.package', string='Default Resource Package')

    @api.model
    def _generate_saas_price_list(self):
        companies = self.env['res.company'].search([])
        default_price_list = self.env['product.pricelist'].sudo().with_context(active_test=False).search(
            [('company_id', 'in', companies.ids)]
        )
        price_list_item_vals_list = []
        for price_list in default_price_list:
            # monthly
            price_list_item_vals_list.append({
                'pricelist_id': price_list.id,
                'base': 'list_price',
                'applied_on': '3_global',
                'price_discount': 0,
                'min_quantity': 0,
                'compute_price': 'formula',
                'subscription_type': 'monthly'
            })
            # yearly
            price_list_item_vals_list.append({
                'pricelist_id': price_list.id,
                'base': 'list_price',
                'applied_on': '3_global',
                'price_discount': 10,
                'min_quantity': 0,
                'compute_price': 'formula',
                'subscription_type': 'yearly'
            })
        self.env['product.pricelist.item'].create(price_list_item_vals_list)
