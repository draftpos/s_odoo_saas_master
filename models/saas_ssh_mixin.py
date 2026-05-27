import logging
import os
from odoo import models


_logger = logging.getLogger(__name__)


class SSHMixin(models.AbstractModel):
    _name = 'saas.ssh.mixin'
    _description = "SaaS SSH Mixin"

    def _exec_cmd(self, command, ssh, arguments=False, without_return=True, raise_on_error=False):
        """
        Execute a command over SSH.
        - arguments: list of strings to feed to stdin (e.g. passwords)
        - without_return: if False, return stdout lines; if True return None
        - raise_on_error: if True, raise UserError if command fails
        Always logs stderr at WARNING level so errors are visible in the log.
        """
        assert isinstance(command, str) and command
        command = command.strip()

        _logger.info("[CMD] %s", command)

        stdin, stdout, stderr = ssh.exec_command(command)

        if arguments:
            for arg in arguments:
                stdin.write('%s\n' % arg)
            stdin.flush()

        exit_code = stdout.channel.recv_exit_status()

        err_output = stderr.read().decode('utf-8', errors='replace').strip()
        if err_output:
            _logger.warning(
                "SSH command stderr (exit=%s) [%s]: %s",
                exit_code, command, err_output
            )
            
        if raise_on_error and exit_code != 0:
            from odoo.exceptions import UserError
            from odoo import _
            raise UserError(_("Command failed with exit code %s: %s\nStderr: %s") % (exit_code, command, err_output))

        if not without_return:
            return stdout.readlines()

    def _create_file(self, ssh, file_path, file_content):
        """
        Write file_content to file_path on the remote server via SFTP.
        Ensures the parent directory exists first using SSH mkdir -p.
        """
        # Derive parent directory using posixpath (not os.path — this runs locally on Linux anyway)
        parent_dir = file_path.rsplit('/', 1)[0] if '/' in file_path else None
        if parent_dir:
            self._exec_cmd('mkdir -p %s' % parent_dir, ssh)

        _logger.info("[FILE] %s", file_path)
        sftp = ssh.open_sftp()
        try:
            sftp.putfo(
                __import__('io').StringIO(file_content),
                file_path,
            )
        finally:
            sftp.close()

        # Verify the file was written
        self._exec_cmd('test -f %s && echo OK' % file_path, ssh)
        return True

    def _create_symlink(self, ssh, from_path, to_path, overwrite=False):
        """Create a symlink on the remote server."""
        cmd = "ln -s%s %s %s" % ('f' if overwrite else ' ', from_path, to_path)
        return self._exec_cmd(cmd, ssh)

    def _get_container_status(self, containers):
        """
        Return a dict of {container_name: status_string} for each container.
        Inspects each container individually so a missing container on the
        server never shifts results and corrupts statuses for the others.
        """
        res = {}
        if not containers:
            return res

        ssh = self._connect()
        try:
            for container in containers:
                try:
                    output = self._exec_cmd(
                        "docker inspect -f '{{.State.Status}}' %s" % container.name,
                        ssh,
                        without_return=False,
                    )
                    res[container.name] = output[0].rstrip() if output else 'not deployed'
                except Exception:
                    res[container.name] = 'not deployed'
                    _logger.warning(
                        "Could not inspect container '%s' — "
                        "it may not exist on the server yet.",
                        container.name,
                    )
        finally:
            ssh.close()

        return res