import logging

from odoo import http
from odoo.http import content_disposition, request
_logger = logging.getLogger(__name__)


class AutoBackup(http.Controller):

    @http.route('/saas_backup/download/<int:backup_id>', type='http', auth="user", methods=['GET'], csrf=False)
    def saas_backup_download(self, backup_id, **kwargs):
        try:
            backup = request.env['saas.odoo.instance.backup'].browse(backup_id)
            headers = [
                ('Content-Type', 'application/octet-stream; charset=binary'),
                ('Content-Disposition', content_disposition(backup.name)),
            ]
            with open(backup.file_path, mode='rb') as f:
                stream = f.read()
            response = request.make_response(stream, headers=headers)
            return response
        except Exception as e:
            error = "Download backup error: %s" % (str(e) or repr(e))
            _logger.exception(error)
