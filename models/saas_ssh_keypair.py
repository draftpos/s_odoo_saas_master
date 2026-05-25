import base64
from odoo import fields, models, api
from odoo.exceptions import UserError


class SSHKeypair(models.Model):
    _name = 'saas.ssh.keypair'
    _description = "SaaS SSH Key Pair"

    name = fields.Char(string='Name', required=True)
    type = fields.Selection([('rsa', 'RSA')], string='Type', required=True, default='rsa')
    active = fields.Boolean(string="Active", default=True)
    public_key_id = fields.Many2one('ir.attachment', string='Public Key')
    private_key_id = fields.Many2one('ir.attachment', string='Private Key')
    private_key_data = fields.Binary(string="Private Key File", store=False)
    private_key_filename = fields.Char(string="Private Key Filename", default="id_rsa")
    can_edit_private_key = fields.Boolean(
        string="Can Edit Private Key",
        compute="_compute_can_edit_private_key",
    )

    def _normalize_key_to_b64(self, data):
        """
        Odoo Binary fields give us base64-encoded bytes.
        ir.attachment.datas also expects base64.
        So we just pass it straight through — no re-encoding needed.

        However, if somehow raw PEM bytes arrive (e.g. from a script),
        we detect that and encode them properly.
        """
        if isinstance(data, bytes):
            # Try to decode as UTF-8 to inspect content
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                # Raw binary — encode to base64 for attachment storage
                return base64.b64encode(data).decode('utf-8')

            # If it's already a PEM key (raw, not encoded), encode it
            if text.strip().startswith('-----BEGIN'):
                return base64.b64encode(data).decode('utf-8')

            # Otherwise assume it's already base64
            return text

        if isinstance(data, str):
            # If it looks like a PEM key someone passed as a string, encode it
            if data.strip().startswith('-----BEGIN'):
                return base64.b64encode(data.encode('utf-8')).decode('utf-8')
            # Already base64 string
            return data

        return data

    @api.model_create_multi
    def create(self, vals_list):
        if isinstance(vals_list, dict):
            vals_list = [vals_list]

        for vals in vals_list:
            if vals.get('private_key_data'):
                datas = self._normalize_key_to_b64(vals['private_key_data'])
                attachment = self.env['ir.attachment'].sudo().create({
                    'name': vals.get('private_key_filename', 'id_rsa'),
                    'type': 'binary',
                    'datas': datas,
                    'res_model': self._name,
                    'res_id': 0,
                    'mimetype': 'application/octet-stream',
                })
                vals['private_key_id'] = attachment.id
                vals.pop('private_key_data', None)
                vals.pop('private_key_filename', None)

        return super().create(vals_list)

    def write(self, vals):
        if vals.get('private_key_data'):
            datas = self._normalize_key_to_b64(vals['private_key_data'])
            filename = vals.get('private_key_filename', 'id_rsa')

            for record in self:
                if record.private_key_id:
                    record.private_key_id.sudo().write({
                        'name': filename,
                        'datas': datas,
                    })
                else:
                    attachment = self.env['ir.attachment'].sudo().create({
                        'name': filename,
                        'type': 'binary',
                        'datas': datas,
                        'res_model': self._name,
                        'res_id': record.id,
                        'mimetype': 'application/octet-stream',
                    })
                    # Use direct SQL to avoid triggering write() recursion
                    record.sudo().with_context(no_recompute=True).write(
                        {'private_key_id': attachment.id}
                    )

        clean_vals = {
            k: v for k, v in vals.items()
            if k not in ('private_key_data', 'private_key_filename')
        }
        return super().write(clean_vals)

    def _compute_can_edit_private_key(self):
        is_saas_master = self.env.user.has_group('s_odoo_saas_master.group_odoo_saas_master')
        for record in self:
            record.can_edit_private_key = is_saas_master

    def get_private_key_pem(self):
        """
        Returns the raw PEM string of the private key, properly decoded.
        Called by saas_pserver._load_private_key().
        """
        self.ensure_one()
        attachment = self.private_key_id
        if not attachment:
            raise UserError(
                "No private key attachment found for SSH Key Pair '%s'." % self.name
            )

        raw_b64 = attachment.sudo().datas
        if not raw_b64:
            raise UserError(
                "Private key attachment '%s' is empty." % attachment.name
            )

        # datas is base64 → decode to bytes → decode to PEM string
        if isinstance(raw_b64, str):
            raw_b64 = raw_b64.encode('utf-8')

        key_bytes = base64.b64decode(raw_b64)

        # If the result is STILL base64 (double-encoded), decode again
        try:
            text = key_bytes.decode('utf-8').strip()
            if not text.startswith('-----BEGIN'):
                # Probably double-encoded — try one more decode
                key_bytes = base64.b64decode(text)
                text = key_bytes.decode('utf-8').strip()
        except Exception:
            raise UserError(
                "Could not decode private key for '%s'. "
                "Please re-upload the key file." % self.name
            )

        if not text.startswith('-----BEGIN'):
            raise UserError(
                "Private key for '%s' does not appear to be a valid PEM file. "
                "Please re-upload the key." % self.name
            )

        return text