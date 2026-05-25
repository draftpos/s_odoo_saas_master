from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    required_product_ids = fields.Many2many('product.product', 'require_product_id', 'product_id', 'require_product_id', string='Requires product')
    dependent_product_ids = fields.Many2many('product.product', 'require_product_id', 'require_product_id', 'product_id', string='Dependent product')

    def get_required_products(self):
        self.ensure_one()

        def get_required(product):
            products = product.required_product_ids
            for p in product.required_product_ids:
                products |= get_required(p)
            return products

        required_products = get_required(self)
        return required_products.ids

    def get_dependent_products(self):
        self.ensure_one()

        def get_dependent(product):
            products = product.dependent_product_ids
            for p in product.dependent_product_ids:
                products |= get_dependent(p)
            return products

        dependent_products = get_dependent(self)
        return dependent_products.ids
