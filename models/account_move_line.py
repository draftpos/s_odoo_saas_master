from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def reconcile(self):
        res = super(AccountMoveLine, self).reconcile()
        for instance in self.move_id.instance_id:
            if all(inv.payment_state == 'paid' for inv in instance.account_move_ids):
                if instance.operation_state != 'deploy' and not instance.buy_now_from_pricing:
                    instance.action_start()
        return res
