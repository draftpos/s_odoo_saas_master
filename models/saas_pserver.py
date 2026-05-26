import paramiko
import json
import logging
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
        privatekey_file_full_path = self.ssh_keypair_id.private_key_id._full_path(
            self.ssh_keypair_id.private_key_id.store_fname
        )
        if not privatekey_file_full_path:
            raise UserError(
                _("Cannot find attachment path of private key of %s server.") % self.name
            )

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                managing_ip,
                username='root',
                port=self.ssh_port,
                key_filename=privatekey_file_full_path,
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

    def _deploy_odoo_instance(self, instance):
        ssh = self._connect()
        try:
            self._create_instance_folder(instance, ssh)
            self._create_odoo_instance_config_file(instance, ssh)
            self._create_custom_addons(instance.custom_addon_ids, ssh)
            
            # Create host PostgreSQL database
            server = instance.odoo_server_id
            self._exec_cmd(
                f"PGPASSWORD='{server.pg_password}' createdb -h {server.pg_host} -U {server.pg_user} -O {server.pg_user} {instance.technical_name} 2>/dev/null || true",
                ssh
            )

            if instance.default_module:
                init_cmd = f"PGPASSWORD='{server.pg_password}' {server.python_path} {server.odoo_bin_path} -c /home/{instance.technical_name}/config/odoo.conf -i {instance.default_module} -d {instance.technical_name} --stop-after-init"
                self._exec_cmd(init_cmd, ssh)
            
            self._create_systemd_service_file(instance, ssh)
            self._systemd_operation(instance, 'start', ssh=ssh)
            self._create_nginx_file(instance.domain_name_ids, ssh)
            ssh.close()
        except Exception as ex:
            try:
                self._revoke_odoo_instance(instance, ssh)
            except Exception:
                pass
            raise UserError(str(ex))

    def _deploy_odoo_instance_from_template(self, instance):
        ssh = self._connect()
        try:
            self._create_instance_folder(instance, ssh)
            self._prepare_instance_folder_from_template(instance, ssh)
            
            # Duplicate the template database natively
            server = instance.odoo_server_id
            template_db = instance.template_instance_id.technical_name
            dup_query = f"CREATE DATABASE {instance.technical_name} TEMPLATE {template_db} OWNER {server.pg_user};"
            dup_cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} -U {server.pg_user} -d postgres -c \"{dup_query}\""
            self._exec_cmd(dup_cmd, ssh)

            self._create_systemd_service_file(instance, ssh)
            self._systemd_operation(instance, 'start', ssh=ssh)
            self._create_nginx_file(instance.domain_name_ids, ssh)
            ssh.close()
        except Exception as ex:
            try:
                self._revoke_odoo_instance(instance, ssh)
            except Exception:
                pass
            raise UserError(str(ex))

    def _prepare_instance_folder_from_template(self, instance, ssh):
        self._exec_cmd("cp -r -a /home/%s/* /home/%s" % (instance.template_instance_id.technical_name, instance.technical_name), ssh)
        self._exec_cmd("rm -rf /home/%s/odoo-web-data/sessions" % instance.technical_name, ssh)

    def _create_instance_folder(self, instance, ssh):
        base = '/home/%s' % instance.technical_name
        for subdir in ['', '/config', '/odoo-web-data', '/custom-addons', '/logs']:
            self._exec_cmd('mkdir -p %s%s' % (base, subdir), ssh)

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
        self._exec_cmd('certbot --non-interactive --nginx --agree-tos -d %s --redirect' % domain_names, ssh)

    def _revoke_odoo_instance(self, instance, ssh=None):
        if not self._is_valid_ssh(ssh):
            ssh = self._connect()
        self._systemd_operation(instance, 'disable', ssh=ssh)
        service_path = instance._get_systemd_service_file_path()
        self._exec_cmd('rm -f %s' % service_path, ssh)
        self._exec_cmd('systemctl daemon-reload', ssh)

        # Drop PostgreSQL database
        server = instance.odoo_server_id
        self._exec_cmd(
            f"PGPASSWORD='{server.pg_password}' dropdb -h {server.pg_host} -U {server.pg_user} {instance.technical_name} 2>/dev/null || true",
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
                cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} -U {server.pg_user} -d {instance.technical_name} -t -c \"{query}\""
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
                cmd = f"PGPASSWORD='{server.pg_password}' psql -h {server.pg_host} -U {server.pg_user} -d {instance.technical_name} -t -A -F '|' -c \"{query}\""
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