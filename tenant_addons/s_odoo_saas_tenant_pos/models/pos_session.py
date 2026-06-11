# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = 'pos.session'

    def action_pos_session_open(self):
        limit_str = self.env['ir.config_parameter'].sudo().get_param('saas.limit_pos_terminals')
        if limit_str:
            try:
                limit = int(limit_str)
                # Count current open/active sessions (exclude self if it's already in the search results)
                active_sessions = self.env['pos.session'].search([
                    ('state', 'in', ('opening_control', 'opened', 'closing_control')),
                    ('id', 'not in', self.ids)
                ])
                if len(active_sessions) + len(self) > limit:
                    raise UserError(_(
                        "POS Terminal Limit Exceeded!\n"
                        "Your subscription plan allows a maximum of %s active POS session(s). "
                        "Currently, you have %s active session(s). "
                        "Please close one of your active sessions before opening a new one, "
                        "or contact support to upgrade your subscription."
                    ) % (limit, len(active_sessions)))
            except ValueError:
                pass
        return super(PosSession, self).action_pos_session_open()
