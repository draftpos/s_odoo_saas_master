import requests
import time
from odoo import http, _
from odoo.http import request
from odoo.tools import groupby
from odoo.exceptions import ValidationError

# ✅ FIXED v19: controller moved from website_sale to main
from odoo.addons.website_sale.controllers.main import WebsiteSale

import logging
_logger = logging.getLogger(__name__)


class Pricing(http.Controller):

    def _get_pricelist_context(self):
        pricelist_context = dict(request.env.context)
        if not pricelist_context.get('pricelist'):
            pricelist = request.website._get_and_cache_current_pricelist()
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

        plans = request.env['saas.plan'].sudo().search([('active', '=', True)], order='sequence, id')
        
        plan_data = []
        for plan in plans:
            monthly_price = 0.0
            yearly_price = 0.0
            
            if plan.monthly_product_id:
                monthly_price = pricelist.with_context(subscription_type='monthly')._get_product_price(plan.monthly_product_id, 1, partner=partner)
            if plan.yearly_product_id:
                yearly_price = pricelist.with_context(subscription_type='yearly')._get_product_price(plan.yearly_product_id, 1, partner=partner)
                
            plan_data.append({
                'id': plan.id,
                'name': plan.name,
                'description': plan.description,
                'limit_pos_terminals': plan.limit_pos_terminals,
                'limit_users': plan.limit_users,
                'monthly_product_id': plan.monthly_product_id.id if plan.monthly_product_id else False,
                'yearly_product_id': plan.yearly_product_id.id if plan.yearly_product_id else False,
                'monthly_price': monthly_price,
                'yearly_price': yearly_price,
            })

        domains = request.env['saas.based.domain'].sudo().search([])

        templates = request.env['saas.odoo.instance'].sudo().search([
            ('is_template', '=', True),
            ('state', '=', 'deploy'),
        ])
        default_template_id = request.website.company_id.backup_restore_instance_id.id

        # Check if we are upgrading/downgrading an existing instance
        instance_id = post.get('instance_id')
        instance = False
        if instance_id:
            instance = request.env['saas.odoo.instance'].sudo().browse(int(instance_id))
            if not instance.exists() or instance.partner_id.id != request.env.user.partner_id.id:
                instance = False

        values = {
            'plans': plan_data,
            'domains': domains,
            'pricelist': pricelist,
            'pricelists': pricelists,
            'templates': templates,
            'default_template_id': default_template_id,
            'instance': instance,
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
            if instance.is_assigned or instance.state == 'deploy':
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
        pricelist = request.website._get_and_cache_current_pricelist()
        subscription_type = post.get('price_by', 'yearly')
        creation_mode = post.get('creation_mode', 'scratch')
        template_instance_id = post.get('template_instance_id', False)
        plan_id = post.get('plan_id')
        instance_id = post.get('instance_id')

        checkout_vals = {
            'subscription_type': subscription_type,
            'pricelist': pricelist,
            'partner': request.env.user.partner_id,
            'sub_domain': post.get('sub_domain'),
            'domain_id': post.get('domain'),
            'creation_mode': creation_mode,
            'template_instance_id': template_instance_id,
        }

        if instance_id:
            if request.session.uid is False:
                return request.redirect('/web/login?redirect=' + request.httprequest.path + '?instance_id=' + str(instance_id))
            instance = request.env['saas.odoo.instance'].sudo().browse(int(instance_id))
            if not instance.exists() or instance.partner_id.id != request.env.user.partner_id.id:
                return request.redirect('/my/saas/odoo-instances')
            checkout_vals.update({
                'instance_id': instance.id,
                'sub_domain': instance.name,
                'domain_id': instance.based_domain_id.id,
            })

        if plan_id:
            checkout_vals['plan_id'] = int(plan_id)
        else:
            num_users = int(post.get('num_users', 1))
            app_ids = []
            for key, val in post.items():
                if key.startswith('app_') and val == 'on':
                    app_id = int(key[4:])
                    app_ids.append(app_id)
            checkout_vals.update({
                'users_count': num_users,
                'app_ids': app_ids,
            })

        order = request.website.create_saas_order(checkout_vals)
        request.session['sale_order_id'] = order.id
        return request.redirect('/shop/checkout?express=1')

    @http.route('/saas/instance/create-trial', type='json', auth='user')
    def instance_create(self, instance_vals, **kwargs):
        # 1. Pool-first check: Try to claim an unassigned instance first!
        user = request.env.user
        password_hash = user.password
        
        claimed_instance = request.env['saas.odoo.instance'].sudo()._try_claim_pool_instance(
            partner=user.partner_id,
            password_hash=password_hash
        )
        
        if claimed_instance:
            expiration_date = request.env['saas.odoo.instance']._get_expiration_date(
                instance_vals.get('subscription_type'), trial=True
            )
            default_app_ids = instance_vals.get('default_app_ids', [])
            app_ids = [int(app_id[4:]) for app_id in default_app_ids]
            apps = request.env['product.product'].sudo().browse(app_ids)
            default_modules = apps.mapped('technical_name')
            default_module = request.env['saas.odoo.instance']._get_default_modules(default_modules)
            
            claimed_instance.write({
                'trial': True,
                'expiration_date': expiration_date,
                'default_module': default_module,
            })
            return {'id': claimed_instance.id}

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
        
        # Check if the subdomain already exists in database
        existing_instance = request.env['saas.odoo.instance'].sudo().search([
            ('name', '=', instance_vals['sub_domain']),
            ('based_domain_id', '=', base_domain_id),
        ], limit=1)
        
        if existing_instance:
            if existing_instance.is_assigned or existing_instance.state == 'deploy':
                raise ValidationError(_("This subdomain is already taken."))
                
            creation_mode = instance_vals.get('creation_mode', 'scratch')
            use_template = False
            template_instance_id = False
            if creation_mode == 'backup_restore':
                req_template_id = instance_vals.get('template_instance_id')
                if req_template_id:
                    template_record = request.env['saas.odoo.instance'].sudo().browse(int(req_template_id))
                    if template_record.exists() and template_record.is_template and template_record.state == 'deploy':
                        use_template = True
                        template_instance_id = template_record.id

                if not use_template:
                    company = request.env.user.partner_id.company_id or request.env.company
                    if company.backup_restore_instance_id:
                        use_template = True
                        template_instance_id = company.backup_restore_instance_id.id
                    else:
                        raise ValidationError(_("The Backup Restore Site is not configured in the SaaS Settings. Please configure it first."))

            expiration_date = request.env['saas.odoo.instance']._get_expiration_date(instance_vals.get('subscription_type'), trial=True)
            default_module = request.env['saas.odoo.instance']._get_default_modules(default_modules)
            
            existing_instance.sudo().write({
                'partner_id': request.env.user.partner_id.id,
                'is_assigned': True,
                'trial': True,
                'expiration_date': expiration_date,
                'use_template': use_template,
                'template_instance_id': template_instance_id,
                'default_module': default_module,
                'state': 'draft',
            })
            
            # Clear user data on the physical server
            existing_instance.pserver_id._clear_instance_user_data(existing_instance)
            existing_instance.action_deploy()
            return {'id': existing_instance.id}

        instance_vals = request.env['saas.odoo.instance'].sudo()._prepare_instance_val_to_create(instance_vals)
        instance = request.env['saas.odoo.instance'].sudo().create(instance_vals)
        instance.action_deploy()
        return {'id': instance.id}

    @http.route([
        '/pricing-plans'
    ], type='http', auth="public", website=True)
    def pricing_plans(self, **post):
        plans = request.env['saas.plan'].sudo().search([('active', '=', True)], order='sequence, id')
        pricelist = request.website._get_and_cache_current_pricelist()
        
        plan_data = []
        for plan in plans:
            monthly_price = 0.0
            yearly_price = 0.0
            
            if plan.monthly_product_id:
                monthly_price = pricelist._get_product_price(plan.monthly_product_id, 1)
            if plan.yearly_product_id:
                yearly_price = pricelist._get_product_price(plan.yearly_product_id, 1)
                
            plan_data.append({
                'id': plan.id,
                'name': plan.name,
                'description': plan.description,
                'limit_pos_terminals': plan.limit_pos_terminals,
                'limit_users': plan.limit_users,
                'monthly_product_id': plan.monthly_product_id.id if plan.monthly_product_id else False,
                'yearly_product_id': plan.yearly_product_id.id if plan.yearly_product_id else False,
                'monthly_price': monthly_price,
                'yearly_price': yearly_price,
            })
            
        templates = request.env['saas.odoo.instance'].sudo().search([
            ('is_template', '=', True),
            ('state', '=', 'deploy'),
        ])
        default_template_id = request.website.company_id.backup_restore_instance_id.id
        domains = request.env['saas.based.domain'].sudo().search([])
        
        # Check if we are upgrading/downgrading an existing instance
        instance_id = post.get('instance_id')
        instance = False
        if instance_id:
            instance = request.env['saas.odoo.instance'].sudo().browse(int(instance_id))
            if not instance.exists() or instance.partner_id.id != request.env.user.partner_id.id:
                instance = False
        
        values = {
            'plans': plan_data,
            'templates': templates,
            'default_template_id': default_template_id,
            'domains': domains,
            'pricelist': pricelist,
            'instance': instance,
        }
        return request.render("s_odoo_saas_master.pricing_plans", values)

    @http.route(['/pricing-plans/checkout'], type='http', methods=['POST'], auth="public", website=True)
    def plans_checkout(self, **post):
        plan_id = post.get('plan_id')
        subscription_type = post.get('price_by', 'yearly')
        instance_id = post.get('instance_id')
        
        if not plan_id:
            return request.redirect('/pricing-plans')
            
        pricelist = request.website._get_and_cache_current_pricelist()
        
        checkout_vals = {
            'plan_id': int(plan_id),
            'subscription_type': subscription_type,
            'pricelist': pricelist,
            'partner': request.env.user.partner_id,
        }
        
        if instance_id:
            # Plan Upgrade/Downgrade Flow for existing instance
            instance = request.env['saas.odoo.instance'].sudo().browse(int(instance_id))
            if not instance.exists() or instance.partner_id.id != request.env.user.partner_id.id:
                return request.redirect('/my/saas/odoo-instances')
            
            checkout_vals.update({
                'instance_id': instance.id,
                'sub_domain': instance.name,
                'domain_id': instance.based_domain_id.id,
            })
        else:
            # New Plan Subscription Flow
            sub_domain = post.get('sub_domain')
            domain_id = post.get('domain')
            creation_mode = post.get('creation_mode', 'scratch')
            template_instance_id = post.get('template_instance_id')
            
            checkout_vals.update({
                'sub_domain': sub_domain,
                'domain_id': int(domain_id) if domain_id else False,
                'creation_mode': creation_mode,
                'template_instance_id': int(template_instance_id) if template_instance_id else False,
            })
            
        order = request.website.create_saas_order(checkout_vals)
        request.session['sale_order_id'] = order.id
        return request.redirect('/shop/checkout?express=1')


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