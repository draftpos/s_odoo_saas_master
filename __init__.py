from . import models
from . import wizard
from . import controllers


def _price_list_post_init_hook(env):
    env['res.company']._generate_saas_price_list()
