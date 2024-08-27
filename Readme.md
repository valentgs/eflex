# E-Flex Updates

## Version updated on August 11th, 2024, at 17:33:27.

The Network Resources has been created. A Network Resource can be, for example, a bus, a transmission line, a transformer, a shunt, etc. A few files have been added to add this new type of resource of the electrical networks for flexmeasures. Also, changes in the database have been made. The changes are described in the next two subsections.

### Changes in the code

A network resource is pretty similar to an asset. So the existing code to create, read, update, and delete assets is used as an inspiration to create the code to create, read, update, and delete network resources. Files have been generated and other files have been modified. These additions and modifications are specified in the next two subsections

#### Generated Files


- flexmeasures/api/common/schemas/network_resources.py
    - class NetworkResourceIdField(fields.Integer)


#### Modified Files


- flexmeasures/\_\_init\_\_.py: imported Network Resources and its Types models.
- flexmeasures/api/v3_0/\_\_init\_\_.py:
