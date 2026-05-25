from odoo import models


class Website(models.Model):
    _inherit = 'website'

    def create_saas_order(self, data):
        vals = self.sudo()._prepare_saas_order_vals(data)
        order = self.env['sale.order'].sudo().create(vals)
        # order.action_quotation_send()
        return order

    def _prepare_saas_order_vals(self, data):
        pricelist = data.get('pricelist')
        subscription_type = data.get('subscription_type')
        partner = data.get('partner')
        sub_domain = data.get('sub_domain')
        domain_id = int(data.get('domain_id'))
        users_count = int(data.get('users_count'))
        app_ids = data.get('app_ids')
        buy_now_from_pricing = data.get('buy_now_from_pricing', False)
        self.ensure_one()

        pricelist = pricelist.with_context(subscription_type=subscription_type)
        order_vals = self._prepare_sale_order_vals(partner_sudo=partner)
        order_vals.update({
            'subscription_type': subscription_type,
            'is_saas_order': True,
            'subdomain': sub_domain,
            'based_domain_id': domain_id,
            'buy_now_from_pricing': True if buy_now_from_pricing == 'on' else False,
        })
        order_line_vals = []

        # Users line
        user_product = self.sudo().env.ref('s_odoo_saas_master.product_saas_user')
        user_price_unit = pricelist._get_product_price(user_product, 1, uom=user_product.uom_id)
        order_line_vals.append((0, 0, {
            'product_id': user_product.id,
            'product_uom_qty': users_count,
            'product_uom': user_product.uom_id.id,
            'price_unit': user_price_unit,
            'tax_id': [(6, 0, user_product.taxes_id.ids)],
        }))

        # Apps lines
        for app_id in app_ids:
            app_product = self.env['product.product'].browse(app_id)
            app_price_unit = pricelist._get_product_price(app_product, 1, uom=app_product.uom_id)
            order_line_vals.append((0, 0, {
                'product_id': app_id,
                'product_uom_qty':1,
                'product_uom': app_product.uom_id.id,
                'price_unit': app_price_unit,
                'tax_id': [(6, 0, app_product.taxes_id.ids)],
            }))

        order_vals['order_line'] = order_line_vals
        return order_vals
