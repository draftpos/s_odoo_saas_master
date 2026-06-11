/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import publicWidget from "@web/public/public_widget";
import { SaaSBlockUI } from "../components/saas_block_ui";
import { session } from "@web/session";
import { rpc } from '@web/core/network/rpc';

const mainComponentRegistry = registry.category("main_components");

publicWidget.registry.SaasPortalPricing = publicWidget.Widget.extend({
	selector: 'form.openerp_enterprise_pricing_form',
    events: {
        'click .openerp_enterprise_pricing_plan_card': '_onSelectPlanCard',
        'click .btn-select-plan-action': '_onSelectPlanCard',
        "click li[data-type='monthly']": '_onSwitchMonthly',
        "click li[data-type='yearly']": '_onSwitchYearly',
        "change .creation_mode_radio": '_onChangeCreationMode',
        "click a.openerp_enterprise_pricing_buy_now": '_onClickBuy',
    },

    init() {
        this._super.apply(this, arguments);
		this.rpc = rpc;
		this.notification = this.bindService("notification");
		this.session = session;
        this.subscriptionType = 'yearly';
        this.selectedPlanId = null;
        this.plans = {};
        this.currency = {};
        this.pricelistId = false;
    },

    start() {
        this.pricelistId = parseInt(this.$('.openerp_enterprise_pricing_pricelist').data('pricelist'));
        this._loadPlansFromDOM();
        this._getPriceList();
        this._super.apply(this, arguments);
    },

    _loadPlansFromDOM() {
        const self = this;
        this.$('.openerp_enterprise_pricing_plan_card').each(function() {
            const $card = $(this);
            const planId = parseInt($card.data('plan-id'));
            self.plans[planId] = {
                id: planId,
                name: $card.data('plan-name'),
                monthlyPrice: parseFloat($card.data('monthly-price') || 0.0),
                yearlyPrice: parseFloat($card.data('yearly-price') || 0.0),
            };
        });
    },
	
	formatNumber(num) {
        return parseFloat((num).toFixed(this.currency.decimal_places || 2)).toLocaleString();
    },
    
    formatPrice(num) {
        if (!this.currency.symbol) return num.toFixed(2);
        const formattedNumber = this.formatNumber(num);
        if (this.currency.position === 'before') {
            return this.currency.symbol + ' ' + formattedNumber;
        } else {
            return formattedNumber + ' ' + this.currency.symbol;
        }
    },
	
	async _getPriceList() {		
		var pricelist = await this.rpc('/pricing/get-saas-pricelist', {pricelist_id: this.pricelistId});
        this.currency = pricelist.currency;
        this._recomputePriceBoard();     
    },
	
	_recomputePriceBoard() {
        if (!this.selectedPlanId) {
            this.$('.openerp_enterprise_pricing_plan_name').text(_t("None"));
            this.$('.openerp_enterprise_pricing_price_monthly').text(this.formatPrice(0.0));
            this.$('.openerp_enterprise_pricing_price_yearly').text(this.formatPrice(0.0));
            this.$('.openerp_enterprise_pricing_price_yearly_in_year').text(this.formatPrice(0.0));
            return;
        }

        const plan = this.plans[this.selectedPlanId];
        if (!plan) return;

        this.$('.openerp_enterprise_pricing_plan_name').text(plan.name);

        if (this.subscriptionType === 'monthly') {
            this.$('.openerp_enterprise_pricing_price_monthly').text(this.formatPrice(plan.monthlyPrice));
        } else {
            this.$('.openerp_enterprise_pricing_price_yearly').text(this.formatPrice(plan.yearlyPrice / 12));
            this.$('.openerp_enterprise_pricing_price_yearly_in_year').text(this.formatPrice(plan.yearlyPrice));
        }
    },
	
	async _onSelectPlanCard(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        
        const $card = $(ev.currentTarget).closest('.openerp_enterprise_pricing_plan_card');
        const planId = parseInt($card.data('plan-id'));
        this.selectedPlanId = planId;
        
        // Update selected plan input
        this.$('#selected_plan_id').val(planId);
        
        // Highlight active card
        this.$('.openerp_enterprise_pricing_plan_card').removeClass('border-primary').addClass('border-200');
        this.$('.openerp_enterprise_pricing_plan_card').find('.plan-check-icon').addClass('d-none');
        this.$('.openerp_enterprise_pricing_plan_card').find('.btn-select-plan-action')
            .removeClass('btn-primary').addClass('btn-outline-primary').text(_t("Select Plan"));
        
        $card.addClass('border-primary').removeClass('border-200');
        $card.find('.plan-check-icon').removeClass('d-none');
        $card.find('.btn-select-plan-action')
            .removeClass('btn-outline-primary').addClass('btn-primary').text(_t("Selected"));
        
        // Hide error alert on board
        this.$('.openerp_enterprise_pricing_error_no_apps').addClass('d-none');
        
        this._recomputePriceBoard();

        // Finish subscription automatically when selecting plan (if domain fields are validated or upgrading)
        const isUpgrade = this.$('input[name="instance_id"]').length > 0;
        if (isUpgrade) {
            if (this.session.user_id === false) {
                this.notification.add(_t('Please login to continue'), { type: 'warning', sticky: true });
                return;
            }
            this.blockUI(_t("Processing your request..."));
            this.$el.submit();
        } else {
            const checkResult = await this._checkDomain();
            if (checkResult) {
                if (this.session.user_id === false) {
                    this.notification.add(_t('Please login to subscribe'), { type: 'warning', sticky: true });
                    return;
                }
                this.blockUI(_t("Processing your request..."));
                this.$el.submit();
            }
        }
    },
	
	_onSwitchMonthly(ev) {
        this.subscriptionType = 'monthly';
        this.$('input[name=price_by]').val("monthly");
        this.$('#monthly_by').prop('checked', true);

        // Update cards price display
        this.$('.plan-price-monthly').removeClass('d-none');
        this.$('.plan-price-yearly').addClass('d-none');
        this.$('.billing-cycle-desc').text(_t("Billed monthly"));

        this._recomputePriceBoard();
    },
	
	_onSwitchYearly(ev) {
        this.subscriptionType = 'yearly';
        this.$('input[name=price_by]').val("yearly");
        this.$('#yearly_by').prop('checked', true);

        // Update cards price display
        this.$('.plan-price-monthly').addClass('d-none');
        this.$('.plan-price-yearly').removeClass('d-none');
        this.$('.billing-cycle-desc').text(_t("Billed annually"));

        this._recomputePriceBoard();
    },

    _onChangeCreationMode(ev) {
        const val = this.$('input[name="creation_mode"]:checked').val();
        if (val === 'backup_restore') {
            this.$('#template_selection_div').removeClass('d-none');
        } else {
            this.$('#template_selection_div').addClass('d-none');
        }
    },
	
	async _checkDomain() {
        // If we are upgrading/downgrading, subdomain input doesn't exist
        if (this.$('input#sub_domain').length === 0) {
            return true;
        }

        this.$('.odoo_domain_picking_error').empty();
        this.$('input#sub_domain').removeClass('has-error');
        var subDomain = this.$('input.openerp_enterprise_pricing_sub_domain').val();
        if (/^\d/.test(subDomain)){
			this.notification.add(_t("Your subdomain cannot start with a number."), { type: 'warning', sticky: true });
			this.$('input#sub_domain').addClass('has-error');
			return false;
		}
        if (!subDomain) {
			this.notification.add(_t("You have to choose a domain for your instance."), { type: 'warning', sticky: true });
            this.$('input#sub_domain').addClass('has-error');
            return false;
        } else if (!/^[a-z0-9\-]+$/g.test(subDomain)) {
            this.notification.add(_t("Your domain can only contain characters from 'a' to 'z', '0' to '9' and '-'."), { type: 'warning', sticky: true });
            this.$('input#sub_domain').addClass('has-error');
            return false;
        }
        var domainId = parseInt(this.$('select.openerp_enterprise_pricing_domain').val());
        this.$('.odoo_domain_picking_error').append($('<i class="fa fa-spinner fa-spin fa-fw"></i>'));
        var result = await this.rpc('/pricing/check-domain', {sub_domain: subDomain, domain_id: domainId});
        this.$('.odoo_domain_picking i.fa-spinner').remove();
        if (result.error) {
            this.notification.add(result.error, { type: 'warning', sticky: true });
            return false;
        }
        return true;
    },

    async _onClickBuy(ev) {
        ev.preventDefault();
        ev.stopPropagation();

        if (!this.selectedPlanId) {
            this.$('.openerp_enterprise_pricing_error_no_apps').removeClass('d-none');
            this.notification.add(_t("Please select a plan first."), { type: 'warning', sticky: true });
            return;
        }

        const isUpgrade = this.$('input[name="instance_id"]').length > 0;
        if (isUpgrade) {
            if (this.session.user_id === false) {
                this.notification.add(_t('Please login to continue'), { type: 'warning', sticky: true });
                return;
            }
            this.blockUI(_t("Processing your request..."));
            this.$el.submit();
        } else {
            const checkResult = await this._checkDomain();
            if (!checkResult) {
                return;
            }
            if (this.session.user_id === false) {
                this.notification.add(_t('Please login to buy now'), { type: 'warning', sticky: true });
                return;
            }
            this.blockUI(_t("Processing your request..."));
            this.$el.submit();
        }
    },
	
	blockUI(message) {
        mainComponentRegistry.add(
            "SaaSBlockUI",
            {
                Component: SaaSBlockUI,
                props: {
                    message,
                },
            },
            { force: true }
        );
    },

    unblockUI() {
        mainComponentRegistry.remove("SaaSBlockUI");
    }

});
