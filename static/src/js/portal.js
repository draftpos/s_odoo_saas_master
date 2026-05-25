/** @odoo-module */

import { PortalHomeCounters } from '@portal/js/portal_home_counters';

PortalHomeCounters.include({
    /**
     * @override
     */
    _getCountersAlwaysDisplayed() {
        return this._super(...arguments).concat(['instance_count']);
    },
});
