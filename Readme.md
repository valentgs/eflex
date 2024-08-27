# E-Flex Updates

## Version updated on August 11th, 2024, at 17:33:27.

_DESCRIBE IT_

The Network Resources UI and terminal functionalities have been created. A Network Resource can be, for example, a bus, a transmission line, a transformer, a shunt, etc. A few files have been added to add this new type of resource of the electrical networks for flexmeasures. Also, changes in the database have been made. The changes are described in the next two subsections.

### Changes in the code

_DESCRIBE IT_

A network resource is similar to an asset. So the existing code to create, read, update, and delete assets is used as an inspiration to create the code to create, read, update, and delete network resources. Files have been generated and other files have been modified. These additions and modifications are specified in the next two subsections

#### Generated Files

_DESCRIBE IT_

- flexmeasures/api/common/schemas/network_resources.py
    - class NetworkResourceIdField(fields.Integer)
- flexmeasures/api/v3_0/network_resources.py
    - class NetworkResourceAPI(FlaskView)
- flexmeasures/data/models/network_resources.py
    - class NetworkResourceType(db.Model), similar 
    - class NetworkResource(db.Model, AuthModelMixin)
- flexmeasures/data/schemas/network_resource.py
    - class JSON(fields.Field)
    - class NetworkResourceSchema(ma.SQLAlchemySchema)
    - class NetworkResourceTypeSchema(ma.SQLAlchemySchema)
    - class NetworkResourceIdField(MarshmallowClickMixin, fields.Int):
- flexmeasures/data/services/opf.py
    - coded functions to run OPF and PF, they can be used both from terminal or the UI
- flexmeasures/ui/crud/networkresources.py
    - class NetworkResourceForm(FlaskForm)
    - class NewNetworkResourceForm(NetworkResourceForm)
    - class NetworkResourceCrudUI(FlaskView)
- flexmeasures/ui/templates/crud/networkresource.html
    - page to show a specific network resource, given by the id
- flexmeasures/ui/templates/crud/networkresource_new.html
    - page that has a form to add a new network resource to the database 
- flexmeasures/ui/templates/crud/networkresources.html
    - page that shows all network resources on the database

#### Modified Files

_DESCRIBE IT_

- flexmeasures/\_\_init\_\_.py
    - imported Network Resources and its Types models
- flexmeasures/api/v3\_0/\_\_init\_\_.py
    - imported Network Resources models and API.
    - registered API into the app
- flexmeasures/cli/data_add.py
    - imported Integer from traitlets
    - imported the opf and pf from the data services
    - imported the network resource and network resource type schemas
    - created a function to add the network resource type through the terminal
    - created a function to add the network resource through the terminal
    - created a function to run OPF/PF through the terminal
- flexmeasures/data/models/annotations.py
    - class NetworkResourceAnnotationRelationship(db.Model)
- flexmeasures/data/queries/annotations.py
    - imported NetworkResourceAnnotationRelationship from model annottions
    - created the network resource function to deal with the queries
- flexmeasures/ui/\_\_init\_\_.py
    - imported Network Resource CRUD UI from the crud
    - registed the CRUD UI to the app
- flexmeasures/ui/templates/base.html
    - added the network resources tab on the navigation bar
- flexmeasures/ui/templates/defaults.jinja
    - added the network resources tab to the navigation bar, with an icon
- flexmeasures/ui/utils/breadcrumb_utils.py
    - added the network resource to the breadcrumb idea (it is not working yet, not finished)
- flexmeasures/ui/utils/view_utils.py
    - added the option to download the data from the webpage
- flexmeasures/utils/unit_utils.py
    - VAr unit has been defined and can be used as a unit for a sensor 

## Version updated on August 22th, 2024, at 15:47:27.

### Changes in the code


#### Generated Files

- flexmeasures/api/common/schemas/networks.py
    - class NetworkIdField(fields.Integer)
- flexmeasures/api/v3_0/networks.py
    - class NetworkAPI(FlaskView)
- flexmeasures/api/v3_0/opf.py
    - class OPFAPI(FlaskView)
- flexmeasures/data/models/networks.py
    - class Network(db.Model, AuthModelMixin):
- flexmeasures/data/schemas/networks.py
    - class JSON(fields.Field)
    - class NetworkSchema(ma.SQLAlchemySchema)
    - class NetworkIdField(MarshmallowClickMixin, fields.Int)
- flexmeasures/ui/crud/networks.py
    - class NetworkForm(FlaskForm)
    - class NewNetworkForm(NetworkForm)
    - class NetworkCrudUI(FlaskView)
- flexmeasures/ui/crud/opf.py
    - class RunOPFUI(FlaskView)
- flexmeasures/ui/templates/admin/opf.html
    - page to show the OPF configuration (network, date, and time)
- flexmeasures/ui/templates/base.html
    - added the network tab into the navigation bar
    - added the opf tab into the the navigation bar
- flexmeasures/ui/templates/crud/network.html
    - page to show a specific network, given by the id
- flexmeasures/ui/templates/crud/network_new.html
    - page that has a form to add a new network to the database 
- flexmeasures/ui/templates/crud/networks.html
    - page that shows all networks on the database
- flexmeasures/ui/views/opf.py
    - created to render the opf page


#### Modified Files

- flexmeasures/\_\_init\_\_.py
    - imported the network model
- flexmeasures/api/v3_0/\_\_init\_\_.py
    - imported the NetworkAPI
    - register the NetworkAPI to the app 
- flexmeasures/cli/data_add.py
    - imported Network Schema from schemas
    - imported Network model from Models
    - created a function to add the network through the terminal
- flexmeasures/cli/data_delete.py
    - imported network resource and network models from models
    - created a function to delete a specific network resource through terminal
    - created a function to delete a specific network through terminal
- flexmeasures/data/models/annotations.py
    - class NetworkAnnotationRelationship(db.Model):
- flexmeasures/data/services/opf.py
    - now opf accepts shunts, transformers and external grids. Also, more attributes are considered
- flexmeasures/ui/\_\_init\_\_.py
    - imported NetworkCrudUI from networks
    - imported RunOPFUI from OPF
    - registered NetworkCrudUI and RunOPFUI to the app
- flexmeasures/ui/crud/networkresources.py
    - corrected some small mistakes
- flexmeasures/ui/templates/defaults.jinja
    - added the network tab on the navigation bar, with an icon
    - added the opf tab on the navigation bar, with an icon
- flexmeasures/ui/utils/breadcrumb_utils.py
    - imported the network
- flexmeasures/ui/views/\_\_init\_\_.py
    - imported opf view from views

















