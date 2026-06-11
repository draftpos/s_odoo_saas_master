import paramiko
import json
import logging
import os
from odoo import fields, models, _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class PServer(models.Model):
    _name = 'saas.pserver'
    _inherit = ['saas.ssh.mixin']
    _description = "SaaS Physical Server"

    name = fields.Char(string='Name', required=True)
    ssh_port = fields.Integer(string="SSH Port", required=True, default=22)
    ssh_keypair_id = fields.Many2one('saas.ssh.keypair', string="SSH Key Pair", required=True)
    ssh_keypair_name = fields.Char(related="ssh_keypair_id.name", string="SSH Key", readonly=True)
    can_edit_ssh_key = fields.Boolean(string="Can Edit SSH Key", compute="_compute_can_edit_ssh_key")
    ip_ids = fields.One2many('saas.pserver.ip', 'pserver_id', string="IPs")
    version_16_plus = fields.Boolean(string='Ubuntu Version 16+', default=True)
    active = fields.Boolean(string="Active", default=True)

    def _compute_can_edit_ssh_key(self):
        """Check if the current user can edit SSH key fields."""
        is_saas_master = self.env.user.has_group('s_odoo_saas_master.group_odoo_saas_master')
        for record in self:
            record.can_edit_ssh_key = is_saas_master

    def action_test_connection(self):
        ssh = self._connect()
        ssh.close()
        message = _("Connection Successful!")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'type': 'success',
                'sticky': False,
            }
        }

    def _get_managing_ip(self):
        managing_ips = self.ip_ids.filtered(lambda ip: ip.type == 'managing_ip')
        if not managing_ips:
            raise UserError(_("Cannot find managing IP of %s server") % self.name)
        return managing_ips[0].name

    def _connect(self):
        managing_ip = self._get_managing_ip()
        
        try:
            pkey = self.ssh_keypair_id.get_private_key_pkey()
        except Exception as e:
            raise UserError(
                _("Cannot load private key for server %s.\n\nError: %s")
                % (self.name, str(e))
            )

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                managing_ip,
                username='root',
                port=self.ssh_port,
                pkey=pkey,
                allow_agent=False,
                look_for_keys=False,
            )
        except Exception as e:
            raise UserError(
                _("Cannot connect to server %s. Please check server information and SSH Key Pair.\n\nError: %s")
                % (self.name, str(e))
            )
        return ssh

    def _is_valid_ssh(self, ssh):
        """Return True only if ssh is a live Paramiko SSHClient."""
        return ssh is not None and hasattr(ssh, 'exec_command')

    def _send_webhook(self, instance, step, message, status="in_progress"):
        """Send real-time progress update to the external webhook."""
        if not instance.webhook_url:
            return
        try:
            import requests
            payload = {
                "instance_id": instance.id,
                "domain": instance.url or instance.name,
                "status": status,
                "current_step": step,
                "message": message,
            }
            requests.post(instance.webhook_url, json=payload, timeout=5)
        except Exception as e:
            _logger.warning("Failed to send webhook for instance %s: %s", instance.id, e)

    def _update_deploy_step(self, instance, step, message):
        """Update the database and fire a webhook."""
        instance.deployment_step = step
        instance.env.cr.commit()
        self._send_webhook(instance, step, message)

    def _deploy_odoo_instance(self, instance):
        ssh = self._connect()
        server = instance.odoo_server_id
        try:
            step = instance.deployment_step or 'init'
            self._send_webhook(instance, step, "Resuming deployment...")

            if step in ['init', 'folders_created']:
                self._create_instance_folder(instance, ssh)
                step = 'folders_created'
                self._update_deploy_step(instance, step, "Instance folders created.")

            if step == 'folders_created':
                self._create_odoo_instance_config_file(instance, ssh)
                self._create_custom_addons(instance.custom_addon_ids, ssh)
                
                # Copy standard tenant SSO and API modules
                odoo_base = os.path.dirname(server.odoo_bin_path) or '/opt/odoo19'
                tenant_addons_src = f"{odoo_base}/custom-addons/s_odoo_saas_master/tenant_addons"
                self._exec_cmd(f"cp -r {tenant_addons_src}/s_odoo_saas_tenant /home/{instance.technical_name}/custom-addons/", ssh, raise_on_error=False)
                self._exec_cmd(f"cp -r {odoo_base}/custom-addons/saas_api /home/{instance.technical_name}/custom-addons/", ssh, raise_on_error=False)
                self._exec_cmd(f"cp -r {tenant_addons_src}/s_odoo_saas_tenant_pos /home/{instance.technical_name}/custom-addons/", ssh, raise_on_error=False)
                
                step = 'config_generated'
                self._update_deploy_step(instance, step, "Odoo configuration generated.")
            
            if step == 'config_generated':
                # Create host PostgreSQL database
                port_arg = f"-p {server.pg_port}" if server.pg_port else ""
                self._exec_cmd(
                    f"PGPASSWORD='{server.pg_password}' createdb -h {server.pg_host} {port_arg} -U {server.pg_user} -O {server.pg_user} {instance.technical_name} 2>/dev/null || true",
                    ssh
                )
                step = 'db_created'
                self._update_deploy_step(instance, step, "PostgreSQL database created.")

            if step == 'db_created':
                modules_to_install = instance.default_module or 's_odoo_saas_tenant'
                    
                self._exec_cmd(f"chown -R {server.pg_user}:{server.pg_user} /home/{instance.technical_name}", ssh)
                
                init_cmd = f"sudo -u {server.pg_user} bash -c \"PGPASSWORD='{server.pg_password}' {server.python_path} {server.odoo_bin_path} -c /home/{instance.technical_name}/config/odoo.conf -i {modules_to_install} -d {instance.technical_name} --stop-after-init\""
                try:
                    self._exec_cmd(init_cmd, ssh, raise_on_error=True)
                except Exception as e:
                    # Ignore harmless gcc profiling errors from rjsmin exit that cause exit code 255
                    if "profiling:" in str(e) and "Cannot open" in str(e):
                        pass
                    else:
                        raise e
                
                self._sync_tenant_credentials(instance, ssh)
                self._sync_tenant_limits(instance, ssh)
                step = 'modules_installed'
                self._update_deploy_step(instance, step, "Odoo modules initialized successfully.")

            if step == 'modules_installed':
                self._create_systemd_service_file(instance, ssh)
                self._systemd_operation(instance, 'start', ssh=ssh)
                self._create_nginx_file(instance.domain_name_ids, ssh)
                self._exec_cmd("systemctl reload nginx", ssh)
                
                step = 'services_started'
                self._update_deploy_step(instance, step, "Systemd and Nginx services started.")

            if step == 'services_started':
                # Post-deployment validation: Verify port is listening
                http_port = instance.port_ids.filtered(lambda p: p.name == 'http_port')
                if http_port:
                    port = http_port[0].port
                    verify_cmd = f"for i in {{1..30}}; do ss -tlnp | grep ':{port}' && exit 0; sleep 1; done; exit 1"
                    self._exec_cmd(verify_cmd, ssh, raise_on_error=True)
                
                step = 'done'
                self._update_deploy_step(instance, step, "Deployment completed successfully.")
                self._send_webhook(instance, step, "Deployment fully verified.", status="completed")

            ssh.close()
        except Exception as ex:
            # Error is handled by api.py outer loop, but we can send a failed webhook here
            self._send_webhook(instance, instance.deployment_step, f"Deployment failed: {str(ex)}", status="failed")
            try:
                self._revoke_odoo_instance(instance, ssh)
            except Exception:
                pass
            raise UserError(str(ex))

    def _deploy_odoo_instance_from_template(self, instance):
        ssh = self._connect()
        server = instance.odoo_server_id
        try:
            step = instance.deployment_step or 'init'
            self._send_webhook(instance, step, "Resuming deployment from template...")

            if step in ['init', 'folders_created']:
                self._create_instance_folder(instance, ssh)
                self._prepare_instance_folder_from_template(instance, ssh)
                
                # Copy standard tenant SSO and API modules
                odoo_base = os.path.dirname(server.odoo_bin_path) or '/opt/odoo19'
                tenant_addons_src = f"{odoo_base}/custom-addons/s_odoo_saas_master/tenant_addons"
                self._exec_cmd(f"cp -r {tenant_addons_src}/s_odoo_saas_tenant /home/{instance.technical_name}/custom-addons/", ssh, raise_on_error=False)
                self._exec_cmd(f"cp -r {odoo_base}/custom-addons/saas_api /home/{instance.technical_name}/custom-addons/", ssh, raise_on_error=False)
                self._exec_cmd(f"cp -r {tenant_addons_src}/s_odoo_saas_tenant_pos /home/{instance.technical_name}/custom-addons/", ssh, raise_on_error=False)
                
                step = 'folders_created'
                self._update_deploy_step(instance, step, "Instance folders prepared from template.")

            if step == 'folders_created':
                # Regenerate the configuration file for the new instance so it doesn't use the template's ports or dbfilter
                self._create_odoo_instance_config_file(instance, ssh)
                step = 'config_generated'
                self._update_deploy_step(instance, step, "Odoo configuration regenerated.")
            
            if step == 'config_generated':
                # Duplicate the template database natively
                template_db = instance.template_instance_id.technical_name
                port_arg = f"-p {server.pg_port}" if server.pg_port else ""

                # 1. Disallow connections to the template database
                allow_false_query = f"ALTER DATABASE {template_db} ALLOW_CONNECTIONS false;"
                allow_false_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d postgres -c \"{allow_false_query}\""
                self._exec_cmd(allow_false_cmd, ssh, raise_on_error=False)

                # 2. Terminate active connections to the template database
                term_query = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{template_db}' AND pid <> pg_backend_pid();"
                term_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d postgres -c \"{term_query}\""
                self._exec_cmd(term_cmd, ssh, raise_on_error=False)

                try:
                    # 3. Duplicate the database with raise_on_error=True
                    dup_query = f"CREATE DATABASE {instance.technical_name} TEMPLATE {template_db} OWNER {server.pg_user};"
                    dup_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d postgres -c \"{dup_query}\""
                    self._exec_cmd(dup_cmd, ssh, raise_on_error=True)
                finally:
                    # 4. Re-allow connections to the template database
                    allow_true_query = f"ALTER DATABASE {template_db} ALLOW_CONNECTIONS true;"
                    allow_true_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d postgres -c \"{allow_true_query}\""
                    self._exec_cmd(allow_true_cmd, ssh, raise_on_error=False)

                # 5. Clear compiled assets from the cloned database to force Odoo to rebuild them,
                # and update the web.base.url system parameter to match the new instance URL.
                sql_updates = [
                    "DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%';",
                    f"UPDATE ir_config_parameter SET value = '{instance.url}' WHERE key = 'web.base.url';"
                ]
                update_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d {instance.technical_name}"
                self._exec_cmd(update_cmd, ssh, arguments=sql_updates, raise_on_error=False)

                step = 'db_created'
                self._update_deploy_step(instance, step, "Database duplicated from template.")

            if step == 'db_created':
                self._create_systemd_service_file(instance, ssh)
                self._exec_cmd(f"chown -R {server.pg_user}:{server.pg_user} /home/{instance.technical_name}", ssh)
                
                # Ensure the SSO module and standard default modules are installed on the cloned database
                modules_to_install = instance.default_module or 's_odoo_saas_tenant'
                install_cmd = f"sudo -u {server.pg_user} bash -c \"PGPASSWORD='{server.pg_password}' {server.python_path} {server.odoo_bin_path} -c /home/{instance.technical_name}/config/odoo.conf -i {modules_to_install} -d {instance.technical_name} --stop-after-init\""
                self._exec_cmd(install_cmd, ssh, raise_on_error=True)
                
                self._sync_tenant_credentials(instance, ssh)
                self._sync_tenant_limits(instance, ssh)
                step = 'modules_installed'
                self._update_deploy_step(instance, step, "Odoo database ready.")

            if step == 'modules_installed':
                self._systemd_operation(instance, 'start', ssh=ssh)
                self._create_nginx_file(instance.domain_name_ids, ssh)
                self._exec_cmd("systemctl reload nginx", ssh)
                step = 'services_started'
                self._update_deploy_step(instance, step, "Systemd and Nginx services started.")
            
            if step == 'services_started':
                # Post-deployment validation: Verify port is listening
                http_port = instance.port_ids.filtered(lambda p: p.name == 'http_port')
                if http_port:
                    port = http_port[0].port
                    verify_cmd = f"for i in {{1..30}}; do ss -tlnp | grep ':{port}' && exit 0; sleep 1; done; exit 1"
                    self._exec_cmd(verify_cmd, ssh, raise_on_error=True)
                
                step = 'done'
                self._update_deploy_step(instance, step, "Deployment completed successfully.")
                self._send_webhook(instance, step, "Deployment fully verified.", status="completed")

            ssh.close()
        except Exception as ex:
            self._send_webhook(instance, instance.deployment_step, f"Deployment failed: {str(ex)}", status="failed")
            try:
                self._revoke_odoo_instance(instance, ssh)
            except Exception:
                pass
            raise UserError(str(ex))

    def _sync_tenant_credentials(self, instance, ssh):
        """
        Synchronize the SaaS Master user's credentials (login and password hash)
        to the administrator user (ID 2) in the newly created tenant database.
        """
        _logger.info("Synchronizing tenant credentials for instance %s", instance.name)
        if not instance.partner_id:
            _logger.warning("No partner linked to instance %s, skipping credentials sync.", instance.name)
            return

        # Find the SaaS master user linked to this partner
        master_user = self.env['res.users'].sudo().search([('partner_id', '=', instance.partner_id.id)], limit=1)
        if not master_user and instance.partner_id.email:
            master_user = self.env['res.users'].sudo().search([('login', '=', instance.partner_id.email)], limit=1)

        if not master_user:
            _logger.warning("No SaaS Master user found for partner %s, skipping credentials sync.", instance.partner_id.name)
            return

        # Fetch password hash directly from SaaS Master database
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [master_user.id])
        row = self.env.cr.fetchone()
        password_hash = row[0] if row else None

        if not password_hash:
            _logger.warning("No password hash found for SaaS Master user %s, skipping password sync.", master_user.login)

        def escape_string(val):
            if val is None:
                return 'NULL'
            return "'" + str(val).replace("'", "''") + "'"

        login_esc = escape_string(master_user.login)
        name_esc = escape_string(master_user.name)
        email_esc = escape_string(master_user.email or master_user.login)

        sql_cmds = []
        if password_hash:
            pwd_esc = escape_string(password_hash)
            sql_cmds.append(f"UPDATE res_users SET login = {login_esc}, password = {pwd_esc} WHERE id = 2;")
        else:
            sql_cmds.append(f"UPDATE res_users SET login = {login_esc} WHERE id = 2;")

        sql_cmds.append(f"UPDATE res_partner SET name = {name_esc}, email = {email_esc} WHERE id = (SELECT partner_id FROM res_users WHERE id = 2);")

        server = instance.odoo_server_id
        port_arg = f"-p {server.pg_port}" if server.pg_port else ""
        psql_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d {instance.technical_name}"

        try:
            self._exec_cmd(psql_cmd, ssh, arguments=sql_cmds, raise_on_error=True)
            _logger.info("Successfully synchronized tenant credentials for instance %s", instance.name)
        except Exception as e:
            _logger.warning("Failed to synchronize tenant credentials for instance %s: %s", instance.name, e)

    def _sync_tenant_limits(self, instance, ssh):
        """
        Synchronize the SaaS plan limits (POS terminals, users) to the tenant database
        as ir.config_parameter records.
        """
        _logger.info("Synchronizing tenant limits for instance %s", instance.name)
        plan_name = instance.plan_id.name if instance.plan_id else 'None'
        limit_pos = instance.limit_pos_terminals or 1
        limit_users = instance.limit_users or 5
        
        sql_cmds = [
            f"INSERT INTO ir_config_parameter (key, value) VALUES ('saas.plan_name', '{plan_name}') ON CONFLICT (key) DO UPDATE SET value = '{plan_name}';",
            f"INSERT INTO ir_config_parameter (key, value) VALUES ('saas.limit_pos_terminals', '{limit_pos}') ON CONFLICT (key) DO UPDATE SET value = '{limit_pos}';",
            f"INSERT INTO ir_config_parameter (key, value) VALUES ('saas.limit_users', '{limit_users}') ON CONFLICT (key) DO UPDATE SET value = '{limit_users}';"
        ]
        
        server = instance.odoo_server_id
        port_arg = f"-p {server.pg_port}" if server.pg_port else ""
        psql_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d {instance.technical_name}"
        
        try:
            self._exec_cmd(psql_cmd, ssh, arguments=sql_cmds, raise_on_error=True)
            _logger.info("Successfully synchronized tenant limits for instance %s", instance.name)
        except Exception as e:
            _logger.warning("Failed to synchronize tenant limits for instance %s: %s", instance.name, e)

    def _sync_tenant_credentials_with_password(self, instance, name, email, password_hash, login=None):
        """
        Sync the given credentials directly to the tenant database admin user (id=2).

        Unlike _sync_tenant_credentials, this method accepts credentials as arguments
        rather than querying them from the master DB, making it safe to call from a
        background thread (where the original ORM cursor may no longer be open).

        Args:
            instance: saas.odoo.instance record
            name: display name to set on the tenant res.partner
            email: email address to set on the tenant res.partner
            password_hash: Odoo-hashed password string (from res_users.password column)
            login: login to set on res_users; defaults to email if omitted
        """
        if not login:
            login = email

        ssh = self._connect()
        try:
            def escape_string(val):
                if val is None:
                    return 'NULL'
                return "'" + str(val).replace("'", "''") + "'"

            login_esc = escape_string(login)
            name_esc = escape_string(name)
            email_esc = escape_string(email)

            sql_cmds = []
            if password_hash:
                pwd_esc = escape_string(password_hash)
                sql_cmds.append(
                    f"UPDATE res_users SET login = {login_esc}, password = {pwd_esc} WHERE id = 2;"
                )
            else:
                sql_cmds.append(
                    f"UPDATE res_users SET login = {login_esc} WHERE id = 2;"
                )
            sql_cmds.append(
                f"UPDATE res_partner SET name = {name_esc}, email = {email_esc} "
                f"WHERE id = (SELECT partner_id FROM res_users WHERE id = 2);"
            )

            server = instance.odoo_server_id
            port_arg = f"-p {server.pg_port}" if server.pg_port else ""
            psql_cmd = (
                f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} "
                f"{port_arg} -U {server.pg_user} -d {instance.technical_name}"
            )
            self._exec_cmd(psql_cmd, ssh, arguments=sql_cmds, raise_on_error=True)
            _logger.info(
                "Pool credential sync succeeded for instance %s (login=%s)",
                instance.name, login
            )
        except Exception as e:
            _logger.warning(
                "Pool credential sync failed for instance %s: %s",
                instance.name, e
            )
        finally:
            ssh.close()

    def _prepare_instance_folder_from_template(self, instance, ssh):
        server = instance.odoo_server_id
        odoo_user = server.pg_user or 'odoo'
        base = '/home/%s' % instance.technical_name

        # Copy all files from the template instance
        self._exec_cmd("cp -r -a /home/%s/* %s" % (instance.template_instance_id.technical_name, base), ssh)

        # Rename the template database name's filestore folder to match the new instance's technical name
        template_db = instance.template_instance_id.technical_name
        new_db = instance.technical_name
        self._exec_cmd(f"if [ -d '{base}/odoo-web-data/filestore/{template_db}' ]; then mv '{base}/odoo-web-data/filestore/{template_db}' '{base}/odoo-web-data/filestore/{new_db}'; fi", ssh)

        # Remove stale sessions from the template and recreate with correct permissions
        self._exec_cmd("rm -rf %s/odoo-web-data/sessions" % base, ssh)
        self._exec_cmd("mkdir -p %s/odoo-web-data/sessions" % base, ssh)

        # Re-apply ownership and permissions for the whole instance directory
        self._exec_cmd('chown -R %s:%s %s' % (odoo_user, odoo_user, base), ssh)
        self._exec_cmd('chmod 755 %s' % base, ssh)
        self._exec_cmd('chmod 755 %s/odoo-web-data' % base, ssh)
        self._exec_cmd('chmod 700 %s/odoo-web-data/sessions' % base, ssh)
        self._exec_cmd('chmod 755 %s/custom-addons' % base, ssh)
        self._exec_cmd('chmod 755 %s/logs' % base, ssh)

    def _create_instance_folder(self, instance, ssh):
        server = instance.odoo_server_id
        odoo_user = server.pg_user or 'odoo'
        base = '/home/%s' % instance.technical_name

        # Create all required directories
        for subdir in ['', '/config', '/odoo-web-data', '/odoo-web-data/sessions', '/custom-addons', '/logs']:
            self._exec_cmd('mkdir -p %s%s' % (base, subdir), ssh)

        # Set correct ownership so the Odoo service (runs as odoo_user) can write
        self._exec_cmd('chown -R %s:%s %s' % (odoo_user, odoo_user, base), ssh)

        # Set directory permissions:
        #  - base & sub-dirs: 755 (owner rwx, group/other rx)
        #  - sessions: 700  (owner rwx only — required by Odoo)
        #  - logs: 755
        self._exec_cmd('chmod 755 %s' % base, ssh)
        self._exec_cmd('chmod 755 %s/config' % base, ssh)
        self._exec_cmd('chmod 755 %s/odoo-web-data' % base, ssh)
        self._exec_cmd('chmod 700 %s/odoo-web-data/sessions' % base, ssh)
        self._exec_cmd('chmod 755 %s/custom-addons' % base, ssh)
        self._exec_cmd('chmod 755 %s/logs' % base, ssh)

    def _create_odoo_instance_config_file(self, instance, ssh):
        file_content = self.env['saas.odoo.instance.config']._get_config_file_content(instance)
        file_path = self.env['saas.odoo.instance.config']._get_config_file_path(instance)
        self._create_file(ssh, file_path, file_content)

    def _create_standard_extra_addons(self, instance, ssh):
        for extra_addon in instance.odoo_server_id.extra_addon_ids:
            self._exec_cmd('cp -r %s /home/%s/custom-addons' % (extra_addon.source_path, instance.technical_name), ssh)

    def _create_custom_addons(self, custom_addons, ssh):
        for custom_addon in custom_addons.filtered(lambda c: not c.cloned):
            cmd = 'git clone %s --branch %s --depth 1 --single-branch %s' % (
                custom_addon.clone_uri, custom_addon.branch, custom_addon.addon_path
            )
            self._exec_cmd(cmd, ssh)

    def _create_systemd_service_file(self, instance, ssh):
        file_content = instance._get_systemd_service_file_content()
        file_path = instance._get_systemd_service_file_path()
        self._create_file(ssh, file_path, file_content)
        self._exec_cmd("systemctl daemon-reload", ssh)

    def _systemd_operation(self, instance, action, ssh=None):
        caller_owns_ssh = self._is_valid_ssh(ssh)
        if not caller_owns_ssh:
            ssh = self._connect()
        service_name = f"odoo-{instance.technical_name}"
        if action == 'start':
            self._exec_cmd(f"systemctl enable {service_name}", ssh)
            self._exec_cmd(f"systemctl start {service_name}", ssh)
        elif action == 'stop':
            self._exec_cmd(f"systemctl stop {service_name}", ssh)
        elif action == 'restart':
            self._exec_cmd(f"systemctl restart {service_name}", ssh)
        elif action == 'disable':
            self._exec_cmd(f"systemctl stop {service_name} 2>/dev/null || true", ssh)
            self._exec_cmd(f"systemctl disable {service_name} 2>/dev/null || true", ssh)
        if not caller_owns_ssh:
            ssh.close()

    def _create_docker_compose_file(self, instance, ssh, odoo_command=False):
        pass

    def _docker_compose_up(self, instance, ssh=None):
        self._systemd_operation(instance, 'start', ssh=ssh)

    def _create_nginx_file(self, domain_name_ids, ssh):
        for domain_name in domain_name_ids:
            file_content = domain_name._get_nginx_file_content()
            file_path = domain_name._get_nginx_file_path()
            symlink_path = domain_name._get_nginx_symlink_file_path()
            self._create_file(ssh, file_path, file_content)
            self._create_symlink(ssh, file_path, symlink_path, overwrite=True)

        self._exec_cmd('systemctl reload nginx', ssh)
        domain_names = ' -d '.join(domain_name_ids.mapped('name'))
        self._exec_cmd('certbot --non-interactive --nginx --agree-tos --register-unsafely-without-email -d %s --redirect' % domain_names, ssh)

    def _revoke_odoo_instance(self, instance, ssh=None):
        if not self._is_valid_ssh(ssh):
            ssh = self._connect()
        self._systemd_operation(instance, 'disable', ssh=ssh)
        service_path = instance._get_systemd_service_file_path()
        self._exec_cmd('rm -f %s' % service_path, ssh)
        self._exec_cmd('systemctl daemon-reload', ssh)

        # Drop PostgreSQL database
        server = instance.odoo_server_id
        port_arg = f"-p {server.pg_port}" if server.pg_port else ""
        self._exec_cmd(
            f"PGPASSWORD='{server.pg_password}' dropdb -h {server.pg_host} {port_arg} -U {server.pg_user} {instance.technical_name} 2>/dev/null || true",
            ssh
        )

        self._remove_instance_folder(instance, ssh)
        self._remove_nginx_file(instance.domain_name_ids, ssh)
        self._remove_network(instance, ssh)
        ssh.close()

    def _remove_docker_containers(self, instance, ssh):
        pass

    def _remove_instance_folder(self, instance, ssh):
        self._exec_cmd('rm -rf /home/%s' % instance.technical_name, ssh)

    def _remove_nginx_file(self, domain_name_ids, ssh):
        if not domain_name_ids:
            return
        need_to_remove = []
        for domain_name in domain_name_ids:
            need_to_remove.append(domain_name._get_nginx_file_path())
            need_to_remove.append(domain_name._get_nginx_symlink_file_path())
        self._exec_cmd('rm -rf %s' % ' '.join(need_to_remove), ssh)
        self._exec_cmd('systemctl reload nginx', ssh)

    def _remove_network(self, instance, ssh):
        pass

    def _get_container_status(self, containers):
        res = {}
        if not containers:
            return res
        ssh = self._connect()
        try:
            for container in containers:
                try:
                    if container.container_type == 'odoo':
                        service_name = f"odoo-{container.instance_id.technical_name}"
                        output = self._exec_cmd(f"systemctl is-active {service_name}", ssh, without_return=False)
                        status = output[0].rstrip() if output else 'inactive'
                        res[container.name] = 'running' if status == 'active' else 'exited'
                    elif container.container_type == 'psql':
                        output = self._exec_cmd("systemctl is-active postgresql || systemctl is-active postgresql-16 || systemctl is-active postgresql-17", ssh, without_return=False)
                        status = output[0].rstrip() if output else 'inactive'
                        res[container.name] = 'running' if status == 'active' else 'exited'
                except Exception:
                    res[container.name] = 'not deployed'
                    _logger.warning("Service '%s' check failed.", container.name)
        finally:
            ssh.close()
        return res

    def _container_operation(self, instance, operation, container_names, ssh=None):
        caller_owns_ssh = self._is_valid_ssh(ssh)
        if not caller_owns_ssh:
            ssh = self._connect()
        self._systemd_operation(instance, operation, ssh=ssh)
        if not caller_owns_ssh:
            ssh.close()

    def _redeploy_odoo_instance_config(self, instance):
        ssh = self._connect()
        try:
            self._remove_odoo_instance_config_file(instance, ssh)
            self._create_odoo_instance_config_file(instance, ssh)
            self._create_systemd_service_file(instance, ssh)
            self._systemd_operation(instance, 'restart', ssh=ssh)
            ssh.close()
        except Exception as ex:
            try:
                ssh.close()
            except Exception:
                pass
            raise UserError(str(ex))

    def _redeploy_odoo_instance_nginx(self, domain_name_ids):
        ssh = self._connect()
        try:
            self._remove_nginx_file(domain_name_ids, ssh)
            self._create_nginx_file(domain_name_ids, ssh)
            ssh.close()
        except Exception as ex:
            try:
                ssh.close()
            except Exception:
                pass
            raise UserError(str(ex))

    def _remove_odoo_instance_config_file(self, instance, ssh):
        config_path = '/home/%s/config/odoo.conf' % instance.technical_name
        self._exec_cmd('rm -f %s' % config_path, ssh)

    def _cancel_nginx(self, domain_name_ids):
        ssh = self._connect()
        self._remove_nginx_file(domain_name_ids, ssh)
        ssh.close()

    def _deploy_nginx(self, domain_name_ids):
        ssh = self._connect()
        self._create_nginx_file(domain_name_ids, ssh)
        ssh.close()

    def _clone_customer_addons(self, custom_addons):
        ssh = self._connect()
        self._create_custom_addons(custom_addons, ssh)
        ssh.close()

    def _pull_customer_addons(self, custom_addons):
        ssh = self._connect()
        for custom_addon in custom_addons:
            _logger.info("Pulling addon: %s", custom_addon.addon_path)
            self._exec_cmd('cd %s && git pull' % custom_addon.addon_path, ssh)
            self._systemd_operation(custom_addon.instance_id, 'restart', ssh=ssh)
        ssh.close()

    def _remove_customer_addons(self, custom_addons):
        ssh = self._connect()
        for custom_addon in custom_addons:
            self._exec_cmd('rm -rf %s' % custom_addon.addon_path, ssh)
            self._systemd_operation(custom_addon.instance_id, 'restart', ssh=ssh)
        ssh.close()

    def _recreate_docker_compose_file(self, instance, odoo_command=False, update=False):
        ssh = self._connect()
        server = instance.odoo_server_id
        if odoo_command:
            cmd_args = odoo_command.replace("odoo", "").strip()
            one_off_cmd = f"PGPASSWORD='{server.pg_password}' {server.python_path} {server.odoo_bin_path} -c /home/{instance.technical_name}/config/odoo.conf {cmd_args} --stop-after-init"
            self._exec_cmd(one_off_cmd, ssh)
        
        self._create_systemd_service_file(instance, ssh)
        self._systemd_operation(instance, 'restart', ssh=ssh)
        ssh.close()

    def _get_active_user(self, instances):
        res = {}
        if not instances:
            return res
        ssh = self._connect()
        try:
            for instance in instances:
                server = instance.odoo_server_id
                query = 'select count(*) from res_users where share=False and active=True'
                port_arg = f"-p {server.pg_port}" if server.pg_port else ""
                cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d {instance.technical_name} -t -c \"{query}\""
                output = self._exec_cmd(cmd, ssh, without_return=False)
                if not output:
                    continue
                for line in output:
                    stripped = line.replace('\n', '').strip()
                    if stripped.isdigit():
                        res[instance.id] = int(stripped)
                        break
        finally:
            ssh.close()
        return res

    def _get_installed_apps(self, instances):
        res = {}
        if not instances:
            return res
        ssh = self._connect()
        try:
            for instance in instances:
                server = instance.odoo_server_id
                query = ("select shortdesc,name,write_date from ir_module_module "
                         "where application=true and state='installed'")
                port_arg = f"-p {server.pg_port}" if server.pg_port else ""
                cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} {port_arg} -U {server.pg_user} -d {instance.technical_name} -t -A -F '|' -c \"{query}\""
                output = self._exec_cmd(cmd, ssh, without_return=False)
                if not output:
                    continue
                apps = []
                for item in output:
                    parts = item.split('|')
                    if len(parts) < 3:
                        continue
                    app_name = parts[0].strip()
                    try:
                        if instance.odoo_version_id.version >= 16:
                            app_name = json.loads(app_name)
                            app_name = list(app_name.values())[0]
                    except Exception:
                        pass
                    technical_name = parts[1].strip()
                    write_date = parts[2].replace('\n', '').strip().split('.')[0]
                    try:
                        installed_date = fields.Datetime.to_datetime(write_date)
                    except Exception:
                        installed_date = fields.Datetime.now()
                    apps.append({
                        'name': app_name,
                        'technical_name': technical_name,
                        'installed_date': installed_date,
                    })
                res[instance.id] = apps
        finally:
            ssh.close()
        return res

    def _create_backup_folder(self, backup_dir):
        ssh = self._connect()
        # FIX: mkdir -p — no error if already exists
        self._exec_cmd('mkdir -p %s' % backup_dir, ssh)
        self._exec_cmd('chmod 755 %s' % backup_dir, ssh)
        ssh.close()

    def _clear_instance_user_data(self, instance):
        ssh = self._connect()
        server = instance.odoo_server_id
        try:
            # 1. Stop systemd service
            self._systemd_operation(instance, 'stop', ssh=ssh)
            
            # 2. Drop database
            port_arg = f"-p {server.pg_port}" if server.pg_port else ""
            self._exec_cmd(
                f"PGPASSWORD='{server.pg_password}' dropdb -h {server.pg_host} {port_arg} -U {server.pg_user} {instance.technical_name} 2>/dev/null || true",
                ssh
            )
            
            # 3. Clear sessions and web data, then recreate with correct permissions
            odoo_user = server.pg_user or 'odoo'
            base = f"/home/{instance.technical_name}"
            self._exec_cmd(f"rm -rf {base}/odoo-web-data/sessions 2>/dev/null || true", ssh)
            self._exec_cmd(f"mkdir -p {base}/odoo-web-data/sessions", ssh)
            self._exec_cmd(f"chown -R {odoo_user}:{odoo_user} {base}/odoo-web-data", ssh)
            self._exec_cmd(f"chmod 755 {base}/odoo-web-data", ssh)
            self._exec_cmd(f"chmod 700 {base}/odoo-web-data/sessions", ssh)
            ssh.close()
        except Exception as ex:
            try:
                ssh.close()
            except Exception:
                pass
            raise UserError(str(ex))