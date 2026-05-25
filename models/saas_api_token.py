import secrets
from odoo import fields, models, api


class SaaSAPIToken(models.Model):
    _name = 'saas.api.token'
    _description = 'SaaS API Token'
    _order = 'create_date desc'

    name = fields.Char(string='Name', required=True, default='API Token')
    token = fields.Char(string='Token', required=True, readonly=True, copy=False)
    partner_id = fields.Many2one('res.partner', string='Partner', required=True, ondelete='cascade')
    active = fields.Boolean(string='Active', default=True)
    last_used = fields.Datetime(string='Last Used', readonly=True)
    expires_at = fields.Datetime(string='Expires At', help="Leave empty for no expiration")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('token'):
                vals['token'] = secrets.token_urlsafe(32)
        return super().create(vals_list)

    def action_regenerate_token(self):
        """Regenerate the API token."""
        for record in self:
            record.token = secrets.token_urlsafe(32)
        return True

    def action_revoke(self):
        """Revoke/deactivate the token."""
        self.write({'active': False})
        return True
