from flask_security.core import current_user
from flask_security import login_required

from flexmeasures.ui.views import flexmeasures_ui
from flexmeasures.data.services.accounts import (
    get_number_of_assets_in_account,
    get_account_roles,
)
from flexmeasures.ui.utils.view_utils import render_flexmeasures_template

from flexmeasures.ui.crud.networks import get_networks_by_account

@flexmeasures_ui.route("/loadscheduling", methods=["GET"])
@login_required
def loadscheduling_view():
    """
    Basic information about the currently logged-in user.
    Plus basic actions (logout, reset pwd)
    """
    account_roles = get_account_roles(current_user.account_id)
    account_role_names = [account_role.name for account_role in account_roles]

    
    networks = get_networks_by_account(current_user.account_id)

    return render_flexmeasures_template(
        "admin/loadscheduling.html",
        logged_in_user=current_user,
        networks=networks,
    )

