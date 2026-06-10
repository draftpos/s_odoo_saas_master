from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def _default_based_domain(self):
        based_domain = self.env['saas.based.domain'].sudo().search([], limit=1)
        return based_domain or False

    is_saas_order = fields.Boolean(string='Is SaaS Order?')
    subscription_type = fields.Selection([
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ], string='Subscription Type', default='yearly', tracking=True)
    subdomain = fields.Char(string="Sub domain", tracking=True, copy=False)
    based_domain_id = fields.Many2one('saas.based.domain', string="Based Domain", tracking=True, copy=False,
        default=_default_based_domain, groups="s_odoo_saas_master.group_odoo_saas_user")
    instance_id = fields.Many2one('saas.odoo.instance', string='Odoo Instance')
    template_instance_id = fields.Many2one('saas.odoo.instance', string='Instance Template')
    creation_mode = fields.Selection([
        ('scratch', 'Create from Scratch'),
        ('backup_restore', 'Restore from Backup'),
    ], string='Creation Mode', default='scratch', tracking=True)
    is_saas_trial = fields.Boolean(related='instance_id.trial', store=True)
    saas_order_type = fields.Selection([
        ('buy_new', 'Buy New'),
        ('renew', 'Renew'),
        ('buy_extra', 'Buy Extra')
    ], default='buy_new', readonly=True, copy=False)
    buy_now_from_pricing = fields.Boolean(help="Technical field")

    @api.constrains('is_saas_order', 'order_line')
    def _check_saas_order_line(self):
        for r in self:
            if r.is_saas_order and r.order_line:
                if r.saas_order_type not in ('buy_extra', 'buy_plan_extra') and not any(line.product_id.is_saas_user for line in r.order_line):
                    raise ValidationError(_("Order lines must include SaaS User product"))

    def _action_confirm(self):
        res = super(SaleOrder, self)._action_confirm()
        InstanceObj = self.env['saas.odoo.instance']
        for r in self:
            if r.is_saas_order:
                if not r.subscription_type:
                    raise ValidationError(_("You must selection Subscription Type."))
                if not r.instance_id and r.saas_order_type == 'buy_new':
                    instance = r._create_odoo_instance()
                    if instance:
                        if not instance.buy_now_from_pricing:
                            instance.action_deploy()
                        r.instance_id = instance.id
                elif r.instance_id and r.saas_order_type == 'renew':
                    expiration_date = InstanceObj._get_expiration_date(r.subscription_type, expiration_date=r.instance_id.expiration_date)
                    r.instance_id.write({'expiration_date': expiration_date, 'trial': False, 'subscription_type': r.subscription_type})
        return res

    def _create_odoo_instance(self):
        if not self.is_saas_order:
            return False

        if not self.subdomain:
            raise ValidationError(_("Cannot find Sub domain to create Odoo instance"))
        if not self.based_domain_id:
            raise ValidationError(_("Cannot find Based domain to create Odoo instance"))

        default_modules = [line.product_id.technical_name for line in self.order_line if not line.product_id.is_saas_user and line.product_id.technical_name]
        
        # Check if the subdomain already exists in database
        existing_instance = self.env['saas.odoo.instance'].sudo().search([
            ('name', '=', self.subdomain),
            ('based_domain_id', '=', self.based_domain_id.id),
        ], limit=1)
        
        if existing_instance:
            if existing_instance.is_assigned or existing_instance.state == 'deploy':
                raise ValidationError(_("Subdomain %s already belongs to Odoo Instance %s and is active.") % (self.subdomain, existing_instance.name))
            
            # Reassign and clear data!
            creation_mode = self.creation_mode or 'scratch'
            use_template = False
            template_instance_id = False
            if creation_mode == 'backup_restore':
                company = self.partner_id.company_id or self.env.company
                if company.backup_restore_instance_id:
                    use_template = True
                    template_instance_id = company.backup_restore_instance_id.id
                else:
                    raise ValidationError(_("The Backup Restore Site is not configured in the SaaS Settings. Please configure it first."))

            expiration_date = self.env['saas.odoo.instance']._get_expiration_date(self.subscription_type, trial=False)
            default_module = self.env['saas.odoo.instance']._get_default_modules(default_modules)
            
            existing_instance.sudo().write({
                'partner_id': self.partner_id.id,
                'is_assigned': True,
                'trial': False,
                'expiration_date': expiration_date,
                'use_template': use_template,
                'template_instance_id': template_instance_id,
                'default_module': default_module,
                'buy_now_from_pricing': self.buy_now_from_pricing,
                'state': 'draft',
            })
            
            # Clear user data on physical server
            existing_instance.pserver_id._clear_instance_user_data(existing_instance)
            return existing_instance

        data = {
            'sub_domain': self.subdomain,
            'partner': self.partner_id,
            'based_domain': self.based_domain_id,
            'subscription_type': self.subscription_type,
            'default_modules': default_modules,
            'buy_now_from_pricing': self.buy_now_from_pricing,
            'creation_mode': self.creation_mode,
            'template_instance_id': self.template_instance_id.id if self.template_instance_id else False,
        }
        instance_vals = self.env['saas.odoo.instance']._prepare_instance_val_to_create(data)
        instance = self.env['saas.odoo.instance'].sudo().create(instance_vals)        
        return instance

    def _create_saas_invoice(self):
        make_invoice = self.env['sale.advance.payment.inv'].with_context(
            active_ids=self.ids,
            active_model='sale.order',
        ).create({
            'advance_payment_method': 'delivered',
        })
        return make_invoice.create_invoices()
