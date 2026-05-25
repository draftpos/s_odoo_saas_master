import requests
import time
from odoo import http, _
from odoo.http import request
from odoo.tools import groupby

# ✅ FIXED v19: controller moved from website_sale to main
from odoo.addons.website_sale.controllers.main import WebsiteSale

import logging
_logger = logging.getLogger(__name__)


class Pricing(http.Controller):

    def _get_pricelist_context(self):
        pricelist_context = dict(request.env.context)
        if not pricelist_context.get('pricelist'):
            pricelist = request.website._get_current_pricelist_sudo()
            pricelist_context['pricelist'] = pricelist.id
        else:
            pricelist = request.env['product.pricelist'].browse(pricelist_context['pricelist'])
        if not pricelist:
            pricelist = request.env['product.pricelist'].search([('company_id', '=', request.website.company_id.id)], limit=1)

        return pricelist_context, pricelist, request.env['product.pricelist'].search([])

    @http.route([
        '''/pricing'''
    ], type='http', auth="public", website=True)
    def pricing(self, **post):
        pricelist_context, pricelist, pricelists = self._get_pricelist_context()
        partner = request.env.user.partner_id
        request.update_context(pricelist=pricelist.id, partner=partner)

        domains = request.env['saas.based.domain'].sudo().search([])

        ProductObj = request.env['product.product'].sudo()

        data = {
            'user': {},
            'categs': []
        }
        user_product = request.env.ref('s_odoo_saas_master.product_saas_user').sudo()
        data['user'].update({
            'id': user_product.id,
            'monthly_price': pricelist.with_context(subscription_type='monthly')._get_product_price(user_product, 1, partner),
            'yearly_price': pricelist.with_context(subscription_type='yearly')._get_product_price(user_product, 1, partner) / 12,
        })
        all_products = ProductObj.search([
            ('is_published', '=', True),
            ('can_be_user_app', '=', True),
            ('is_saas_user', '=', False),
        ], order='website_sequence, id')

        # ✅ FIXED v19: groupby returns an iterator of (key, list) tuples — convert products to list
        for cate, products in groupby(all_products, key=lambda p: p.ecom_category_id):
            products = list(products)
            if not products:
                continue
            data['categs'].append({
                'id': cate.id,
                'name': cate.name,
                'apps': [{
                    'id': product.id,
                    'name': product.name,
                    'tech_name': product.technical_name,
                    'image': request.website.image_url(product, 'image_256'),
                    'monthly_price': pricelist.with_context(subscription_type='monthly')._get_product_price(product, 1),
                    'yearly_price': pricelist.with_context(subscription_type='yearly')._get_product_price(product, 1) / 12,
                } for product in products]
            })

        values = {
            'domains': domains,
            'pricelist': pricelist,
            'pricelists': pricelists,
            'data': data,
        }
        return request.render("s_odoo_saas_master.pricing", values)

    @http.route(['/pricing/get-saas-pricelist'], type='json', auth='public')
    def get_saas_pricelist(self, pricelist_id):
        products = request.env['product.product'].sudo().search([('is_published', '=', True)])
        products |= request.env.ref('s_odoo_saas_master.product_saas_user').sudo()
        pricelist = request.env['product.pricelist'].sudo().browse(pricelist_id)
        # ✅ FIXED v19: removed unused `qty` variable
        monthly_pricelist = {
            p.id: pricelist.with_context(subscription_type='monthly')._get_product_price(p, 1)
            for p in products
        }
        yearly_pricelist = {
            p.id: pricelist.with_context(subscription_type='yearly')._get_product_price(p, 1)
            for p in products
        }
        return {
            'monthly_pricelist': monthly_pricelist,
            'yearly_pricelist': yearly_pricelist,
            'currency': {
                'id': pricelist.currency_id.id,
                'symbol': pricelist.currency_id.symbol,
                'decimal_places': pricelist.currency_id.decimal_places,
                'position': pricelist.currency_id.position or 'after',
            },
        }

    @http.route(['/pricing/get-required-apps'], type='json', auth='public')
    def get_required_apps(self, app_id):
        product = request.env['product.product'].sudo().browse(app_id)
        return product.get_required_products()

    @http.route(['/pricing/get-dependent-apps'], type='json', auth='public')
    def get_dependent_apps(self, app_id):
        product = request.env['product.product'].sudo().browse(app_id)
        return product.get_dependent_products()

    @http.route(['/pricing/check-domain'], type='json', auth='public')
    def check_saas_domain(self, sub_domain, domain_id):
        instance = request.env['saas.odoo.instance'].sudo().search([
            ('name', '=', sub_domain),
            ('based_domain_id', '=', domain_id),
        ], limit=1)
        if instance:
            error = _("Your sub-domain has already been taken. Please choose another one.")
            return {
                'success': False,
                'error': error,
            }
        return {'success': True}

    @http.route(['/pricing/check-trial'], type='json', auth='user', website=True)
    def check_trial(self):
        if request.env.user.partner_id.trial_instance_count >= request.website.company_id.limit_trial:
            return False
        return True

    @http.route(['/pricing/checkout'], type='http', methods=['POST'], auth="public", website=True)
    def checkout(self, **post):
        pricelist = request.website._get_current_pricelist_sudo()
        num_users = int(post.pop('num_users', 1))
        subscription_type = post.pop('price_by', 'yearly')
        app_ids = []
        for key, val in post.items():
            if key.startswith('app_') and val == 'on':
                app_id = int(key[4:])
                app_ids.append(app_id)

        post['partner'] = request.env.user.partner_id
        post['domain_id'] = post.get('domain')
        post['users_count'] = num_users
        post['app_ids'] = app_ids
        post['subscription_type'] = subscription_type
        post['pricelist'] = pricelist
        order = request.website.create_saas_order(post)
        request.session['sale_order_id'] = order.id
        return request.redirect('/shop/checkout?express=1')

    @http.route('/saas/instance/create-trial', type='json', auth='user')
    def instance_create(self, instance_vals, **kwargs):
        base_domain_id = instance_vals['base_domain_id']
        base_domain = request.env['saas.based.domain'].sudo().browse(base_domain_id)

        default_app_ids = instance_vals['default_app_ids']
        app_ids = []
        for app_id in default_app_ids:
            app_ids.append(int(app_id[4:]))
        apps = request.env['product.product'].sudo().browse(app_ids)
        default_modules = apps.mapped('technical_name')

        instance_vals['base_domain'] = base_domain
        instance_vals['default_modules'] = default_modules
        instance_vals['partner'] = request.env.user.partner_id
        instance_vals['trial'] = True
        instance_vals = request.env['saas.odoo.instance'].sudo()._prepare_instance_val_to_create(instance_vals)
        instance = request.env['saas.odoo.instance'].sudo().create(instance_vals)
        instance.action_deploy()
        return {'id': instance.id}


NON_REQUIRED_FIELDS = ['street', 'city']


class SaasPayment(WebsiteSale):

    # ✅ FIXED v19: added methods=['GET'] explicitly as required by v19 routing
    @http.route(['/shop/confirmation'], type='http', methods=['GET'], auth='public', website=True)
    def shop_payment_confirmation(self, **post):
        """ End of checkout process controller. Confirmation is basically seeing
        the status of a sale.order. State at this point:

         - should not have any context / session info: clean them
         - take a sale.order id, because we request a sale.order and are not
           session dependant anymore
        """
        sale_order_id = request.session.get('sale_last_order_id')
        if sale_order_id:
            order = request.env['sale.order'].sudo().browse(sale_order_id)
            if order.is_saas_order and order.instance_id:
                try:
                    if order.invoice_status == 'to invoice':
                        invoice = order._create_saas_invoice()
                        invoice._post()
                        invoice._auto_paid_saas_invoice()
                    return request.redirect('/my/saas/odoo-instance/%s' % order.instance_id.id)
                except Exception as ex:
                    order.instance_id._action_cancel()
                    order.instance_id.unlink()
                    _logger.exception(ex)

        # ✅ FIXED v19: pass **post as kwargs, not post=post
        return super(SaasPayment, self).shop_payment_confirmation(**post)

    def _redirect_instance_url(self, instance):
        response = requests.get(instance.url)
        tried_count = 1
        while response.status_code != 200 and tried_count <= 15:
            time.sleep(2)
            response = requests.get(instance.url)
            tried_count += 1  # ✅ FIXED: missing increment caused infinite loop
        return request.redirect(instance.url, local=False)

    def _get_country_related_render_values(self, kw, render_values):
        res = super(SaasPayment, self)._get_country_related_render_values(kw, render_values)
        order = render_values['website_sale_order']
        res['lang'] = order.partner_id.lang
        res['languages'] = request.env['res.lang'].get_installed()
        return res

    def _get_mandatory_address_fields(self, country_sudo=False):
        req = super(SaasPayment, self)._get_mandatory_address_fields(country_sudo)
        req = list(set(req) - set(NON_REQUIRED_FIELDS))
        return req