from odoo import models


class Website(models.Model):
    _inherit = 'website'

    def create_saas_order(self, data):
        if 'plan_id' in data and data.get('plan_id'):
            vals = self.sudo()._prepare_saas_plan_order_vals(data)
        else:
            vals = self.sudo()._prepare_saas_order_vals(data)
        order = self.env['sale.order'].sudo().create(vals)
        # order.action_quotation_send()
        return order

    def _prepare_saas_plan_order_vals(self, data):
        self.ensure_one()
        pricelist = data.get('pricelist')
        subscription_type = data.get('subscription_type')
        partner = data.get('partner')
        sub_domain = data.get('sub_domain')
        domain_id = int(data.get('domain_id')) if data.get('domain_id') else False
        creation_mode = data.get('creation_mode', 'scratch')
        template_instance_id = data.get('template_instance_id')
        plan_id = int(data.get('plan_id'))
        instance_id = data.get('instance_id')

        plan = self.env['saas.plan'].sudo().browse(plan_id)
        product = plan.monthly_product_id if subscription_type == 'monthly' else plan.yearly_product_id
        if not product:
            from odoo.exceptions import ValidationError
            raise ValidationError("Selected plan does not have a product configured for the chosen billing cycle.")

        pricelist = pricelist.with_context(subscription_type=subscription_type)
        order_vals = self._prepare_sale_order_vals(partner_sudo=partner)
        order_vals.update({
            'subscription_type': subscription_type,
            'is_saas_order': True,
            'subdomain': sub_domain,
            'based_domain_id': domain_id,
            'buy_now_from_pricing': True,
            'creation_mode': creation_mode,
            'template_instance_id': int(template_instance_id) if template_instance_id else False,
            'plan_id': plan.id,
            'saas_order_type': 'change_plan' if instance_id else 'buy_new',
        })
        if instance_id:
            order_vals['instance_id'] = int(instance_id)

        price_unit = pricelist._get_product_price(product, 1, uom=product.uom_id)
        order_vals['order_line'] = [(0, 0, {
            'product_id': product.id,
            'product_uom_qty': 1,
            'product_uom_id': product.uom_id.id,
            'price_unit': price_unit,
            'tax_id': [(6, 0, product.taxes_id.ids)],
        })]
        return order_vals

    def _prepare_saas_order_vals(self, data):
        pricelist = data.get('pricelist')
        subscription_type = data.get('subscription_type')
        partner = data.get('partner')
        sub_domain = data.get('sub_domain')
        domain_id = int(data.get('domain_id'))
        users_count = int(data.get('users_count'))
        app_ids = data.get('app_ids')
        buy_now_from_pricing = data.get('buy_now_from_pricing', False)
        creation_mode = data.get('creation_mode', 'scratch')
        template_instance_id = data.get('template_instance_id')
        self.ensure_one()

        pricelist = pricelist.with_context(subscription_type=subscription_type)
        order_vals = self._prepare_sale_order_vals(partner_sudo=partner)
        order_vals.update({
            'subscription_type': subscription_type,
            'is_saas_order': True,
            'subdomain': sub_domain,
            'based_domain_id': domain_id,
            'buy_now_from_pricing': True if buy_now_from_pricing == 'on' else False,
            'creation_mode': creation_mode,
            'template_instance_id': int(template_instance_id) if template_instance_id else False,
        })
        order_line_vals = []

        # Users line
        user_product = self.sudo().env.ref('s_odoo_saas_master.product_saas_user')
        user_price_unit = pricelist._get_product_price(user_product, 1, uom=user_product.uom_id)
        order_line_vals.append((0, 0, {
            'product_id': user_product.id,
            'product_uom_qty': users_count,
            'product_uom_id': user_product.uom_id.id,
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
                'product_uom_id': app_product.uom_id.id,
                'price_unit': app_price_unit,
                'tax_id': [(6, 0, app_product.taxes_id.ids)],
            }))

        order_vals['order_line'] = order_line_vals
        return order_vals
