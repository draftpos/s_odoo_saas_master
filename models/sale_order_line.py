from odoo import models, api

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends('product_id', 'product_uom_qty', 'product_uom_id', 'order_id.pricelist_id')
    def _compute_pricelist_item_id(self):
        for line in self:
            if not line.product_id or line.display_type:
                line.pricelist_item_id = False
                continue

            pricelist = line.order_id.pricelist_id
            if not pricelist:
                line.pricelist_item_id = False
                continue

            ctx = {}
            if line.order_id.is_saas_order:
                ctx['subscription_type'] = line.order_id.subscription_type

            # ✅ Always use product_uom_id in Odoo 19
            uom = line.product_uom_id

            rule_id = pricelist.with_context(**ctx)._get_product_rule(
                line.product_id,
                quantity=line.product_uom_qty or 1.0,
                uom=uom,
                date=line.order_id.date_order,
            )

            line.pricelist_item_id = rule_id