from odoo import fields, models, api


class OdooIstanceDockerContainer(models.Model):
    _name = 'saas.odoo.instance.docker.container'
    _description = "SaaS Odoo Instance Docker Container"

    instance_id = fields.Many2one('saas.odoo.instance', string='Odoo Instance', required=True, ondelete='cascade')
    name = fields.Char(string='Name', required=True)
    image = fields.Char(string='Image', required=True)
    container_type = fields.Selection([
        ('odoo', 'Odoo'),
        ('psql', 'PSQL')
    ], string='Container Type', required=True)
    ports = fields.Char(string='Ports', compute='_compute_ports', store=True)
    docker_compose_volume_ids = fields.One2many('saas.odoo.instance.docker.compose.volume', 'container_id', string='Volumes')
    state = fields.Selection([
        ('unknown', 'Unknown'),
        ('run', 'Running'),
        ('stop', 'Stopped')
    ], string="Status", compute='_compute_state', compute_sudo=True)

    @api.depends(
        'container_type', 'instance_id',
        'instance_id.docker_xmlrpc_expose_port', 'instance_id.docker_xmlrpcs_expose_port', 'instance_id.docker_longpolling_expose_port',
        'instance_id.docker_xmlrpc_container_port', 'instance_id.docker_xmlrpcs_container_port', 'instance_id.docker_longpolling_container_port')
    def _compute_ports(self):
        for r in self:
            ports = 'Unknown'
            if r.instance_id:
                if r.container_type == 'odoo' and r.instance_id.docker_xmlrpc_expose_port:
                    ports = '%s->%s' % (r.instance_id.docker_xmlrpc_expose_port, r.instance_id.docker_xmlrpc_container_port)
                    ports += ', %s->%s' % (r.instance_id.docker_xmlrpcs_expose_port, r.instance_id.docker_xmlrpcs_container_port)
                    ports += ', %s->%s' % (r.instance_id.docker_longpolling_expose_port, r.instance_id.docker_longpolling_container_port)
                elif r.container_type == 'psql':
                    ports = '5432'
                r.ports = ports

    def _compute_state(self):
        results = {}
        for pserver in self.instance_id.pserver_id:
            containers = self.instance_id.filtered(lambda i: i.pserver_id == pserver and i.state in ('deploy', 'suspend')).docker_container_ids
            results.update(pserver._get_container_status(containers))
        for r in self:
            status = results.get(r.name, False)
            if status == 'running':
                r.state = 'run'
            elif status == 'exited':
                r.state = 'stop'
            else:
                r.state = 'unknown'

    def action_restart(self):
        for r in self:
            if r.container_type == 'odoo':
                r.instance_id.pserver_id._systemd_operation(r.instance_id, 'restart')

    def action_stop(self):
        for r in self:
            if r.container_type == 'odoo':
                r.instance_id.pserver_id._systemd_operation(r.instance_id, 'stop')
                r.instance_id.write({'operation_state': 'stop'})

    def action_start(self):
        for r in self:
            if r.container_type == 'odoo':
                r.instance_id.pserver_id._systemd_operation(r.instance_id, 'start')
                r.instance_id.write({'state': 'deploy', 'operation_state': 'run'})