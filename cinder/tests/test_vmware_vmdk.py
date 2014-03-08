# Copyright (c) 2013 VMware, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Test suite for VMware VMDK driver.
"""

import mock
import mox

from cinder import exception
from cinder.image import glance
from cinder import test
from cinder import units
from cinder.volume import configuration
from cinder.volume.drivers.vmware import api
from cinder.volume.drivers.vmware import error_util
from cinder.volume.drivers.vmware import vim
from cinder.volume.drivers.vmware import vim_util
from cinder.volume.drivers.vmware import vmdk
from cinder.volume.drivers.vmware import vmware_images
from cinder.volume.drivers.vmware import volumeops


class FakeVim(object):
    @property
    def service_content(self):
        return mox.MockAnything()

    @property
    def client(self):
        return mox.MockAnything()

    def Login(self, session_manager, userName, password):
        return mox.MockAnything()

    def Logout(self, session_manager):
        pass

    def TerminateSession(self, session_manager, sessionId):
        pass


class FakeTaskInfo(object):
    def __init__(self, state, result=None):
        self.state = state
        self.result = result

        class FakeError(object):
            def __init__(self):
                self.localizedMessage = None

        self.error = FakeError()


class FakeMor(object):
    def __init__(self, type, val):
        self._type = type
        self.value = val


class FakeObject(object):
    def __init__(self):
        self._fields = {}

    def __setitem__(self, key, value):
        self._fields[key] = value

    def __getitem__(self, item):
        return self._fields[item]


class FakeManagedObjectReference(object):
    def __init__(self, lis=[]):
        self.ManagedObjectReference = lis


class FakeDatastoreSummary(object):
    def __init__(self, freeSpace, capacity, datastore=None, name=None):
        self.freeSpace = freeSpace
        self.capacity = capacity
        self.datastore = datastore
        self.name = name


class FakeSnapshotTree(object):
    def __init__(self, tree=None, name=None,
                 snapshot=None, childSnapshotList=None):
        self.rootSnapshotList = tree
        self.name = name
        self.snapshot = snapshot
        self.childSnapshotList = childSnapshotList


class FakeElem(object):
    def __init__(self, prop_set=None):
        self.propSet = prop_set


class FakeProp(object):
    def __init__(self, name=None, val=None):
        self.name = name
        self.val = val


class FakeRetrieveResult(object):
    def __init__(self, objects, token):
        self.objects = objects
        self.token = token


class FakeObj(object):
    def __init__(self, obj=None):
        self.obj = obj


class VMwareEsxVmdkDriverTestCase(test.TestCase):
    """Test class for VMwareEsxVmdkDriver."""

    IP = 'localhost'
    USERNAME = 'username'
    PASSWORD = 'password'
    VOLUME_FOLDER = 'cinder-volumes'
    API_RETRY_COUNT = 3
    TASK_POLL_INTERVAL = 5.0
    IMG_TX_TIMEOUT = 10
    MAX_OBJECTS = 100

    def setUp(self):
        super(VMwareEsxVmdkDriverTestCase, self).setUp()
        self._config = mox.MockObject(configuration.Configuration)
        self._config.append_config_values(mox.IgnoreArg())
        self._config.vmware_host_ip = self.IP
        self._config.vmware_host_username = self.USERNAME
        self._config.vmware_host_password = self.PASSWORD
        self._config.vmware_wsdl_location = None
        self._config.vmware_volume_folder = self.VOLUME_FOLDER
        self._config.vmware_api_retry_count = self.API_RETRY_COUNT
        self._config.vmware_task_poll_interval = self.TASK_POLL_INTERVAL
        self._config.vmware_image_transfer_timeout_secs = self.IMG_TX_TIMEOUT
        self._config.vmware_max_objects_retrieval = self.MAX_OBJECTS
        self._driver = vmdk.VMwareEsxVmdkDriver(configuration=self._config)
        api_retry_count = self._config.vmware_api_retry_count,
        task_poll_interval = self._config.vmware_task_poll_interval,
        self._session = api.VMwareAPISession(self.IP, self.USERNAME,
                                             self.PASSWORD, api_retry_count,
                                             task_poll_interval,
                                             create_session=False)
        self._volumeops = volumeops.VMwareVolumeOps(self._session,
                                                    self.MAX_OBJECTS)
        self._vim = FakeVim()

    def test_retry(self):
        """Test Retry."""

        class TestClass(object):

            def __init__(self):
                self.counter1 = 0
                self.counter2 = 0

            @api.Retry(max_retry_count=2, inc_sleep_time=0.001,
                       exceptions=(Exception))
            def fail(self):
                self.counter1 += 1
                raise exception.CinderException('Fail')

            @api.Retry(max_retry_count=2)
            def success(self):
                self.counter2 += 1
                return self.counter2

        test_obj = TestClass()
        self.assertRaises(exception.CinderException, test_obj.fail)
        self.assertEqual(test_obj.counter1, 3)
        ret = test_obj.success()
        self.assertEqual(1, ret)

    def test_create_session(self):
        """Test create_session."""
        m = self.mox
        m.StubOutWithMock(api.VMwareAPISession, 'vim')
        self._session.vim = self._vim
        m.ReplayAll()
        self._session.create_session()
        m.UnsetStubs()
        m.VerifyAll()

    def test_do_setup(self):
        """Test do_setup."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'session')
        self._driver.session = self._session
        m.ReplayAll()
        self._driver.do_setup(mox.IgnoreArg())
        m.UnsetStubs()
        m.VerifyAll()

    def test_check_for_setup_error(self):
        """Test check_for_setup_error."""
        self._driver.check_for_setup_error()

    def test_get_volume_stats(self):
        """Test get_volume_stats."""
        stats = self._driver.get_volume_stats()
        self.assertEqual(stats['vendor_name'], 'VMware')
        self.assertEqual(stats['driver_version'], self._driver.VERSION)
        self.assertEqual(stats['storage_protocol'], 'LSI Logic SCSI')
        self.assertEqual(stats['reserved_percentage'], 0)
        self.assertEqual(stats['total_capacity_gb'], 'unknown')
        self.assertEqual(stats['free_capacity_gb'], 'unknown')

    def test_create_volume(self):
        """Test create_volume."""
        driver = self._driver
        host = mock.sentinel.host
        rp = mock.sentinel.resource_pool
        folder = mock.sentinel.folder
        summary = mock.sentinel.summary
        driver._select_ds_for_volume = mock.MagicMock()
        driver._select_ds_for_volume.return_value = (host, rp, folder,
                                                     summary)
        # invoke the create_volume call
        volume = {'name': 'fake_volume'}
        driver.create_volume(volume)
        # verify calls made
        driver._select_ds_for_volume.assert_called_once_with(volume)

        # test create_volume call when _select_ds_for_volume fails
        driver._select_ds_for_volume.side_effect = error_util.VimException('')
        self.assertRaises(error_util.VimFaultException, driver.create_volume,
                          volume)

    def test_success_wait_for_task(self):
        """Test successful wait_for_task."""
        m = self.mox
        m.StubOutWithMock(api.VMwareAPISession, 'vim')
        self._session.vim = self._vim
        result = FakeMor('VirtualMachine', 'my_vm')
        success_task_info = FakeTaskInfo('success', result=result)
        m.StubOutWithMock(vim_util, 'get_object_property')
        vim_util.get_object_property(self._session.vim,
                                     mox.IgnoreArg(),
                                     'info').AndReturn(success_task_info)

        m.ReplayAll()
        ret = self._session.wait_for_task(mox.IgnoreArg())
        self.assertEqual(ret.result, result)
        m.UnsetStubs()
        m.VerifyAll()

    def test_failed_wait_for_task(self):
        """Test failed wait_for_task."""
        m = self.mox
        m.StubOutWithMock(api.VMwareAPISession, 'vim')
        self._session.vim = self._vim
        failed_task_info = FakeTaskInfo('failed')
        m.StubOutWithMock(vim_util, 'get_object_property')
        vim_util.get_object_property(self._session.vim,
                                     mox.IgnoreArg(),
                                     'info').AndReturn(failed_task_info)

        m.ReplayAll()
        self.assertRaises(error_util.VimFaultException,
                          self._session.wait_for_task,
                          mox.IgnoreArg())
        m.UnsetStubs()
        m.VerifyAll()

    def test_delete_volume_without_backing(self):
        """Test delete_volume without backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        self._volumeops.get_backing('hello_world').AndReturn(None)

        m.ReplayAll()
        volume = FakeObject()
        volume['name'] = 'hello_world'
        self._driver.delete_volume(volume)
        m.UnsetStubs()
        m.VerifyAll()

    def test_delete_volume_with_backing(self):
        """Test delete_volume with backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops

        backing = FakeMor('VirtualMachine', 'my_vm')
        task = FakeMor('Task', 'my_task')

        m.StubOutWithMock(self._volumeops, 'get_backing')
        m.StubOutWithMock(self._volumeops, 'delete_backing')
        self._volumeops.get_backing('hello_world').AndReturn(backing)
        self._volumeops.delete_backing(backing)

        m.ReplayAll()
        volume = FakeObject()
        volume['name'] = 'hello_world'
        self._driver.delete_volume(volume)
        m.UnsetStubs()
        m.VerifyAll()

    def test_create_export(self):
        """Test create_export."""
        self._driver.create_export(mox.IgnoreArg(), mox.IgnoreArg())

    def test_ensure_export(self):
        """Test ensure_export."""
        self._driver.ensure_export(mox.IgnoreArg(), mox.IgnoreArg())

    def test_remove_export(self):
        """Test remove_export."""
        self._driver.remove_export(mox.IgnoreArg(), mox.IgnoreArg())

    def test_terminate_connection(self):
        """Test terminate_connection."""
        self._driver.terminate_connection(mox.IgnoreArg(), mox.IgnoreArg(),
                                          force=mox.IgnoreArg())

    def test_create_backing_in_inventory_multi_hosts(self):
        """Test _create_backing_in_inventory scanning multiple hosts."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        host1 = FakeObj(obj=FakeMor('HostSystem', 'my_host1'))
        host2 = FakeObj(obj=FakeMor('HostSystem', 'my_host2'))
        retrieve_result = FakeRetrieveResult([host1, host2], None)
        m.StubOutWithMock(self._volumeops, 'get_hosts')
        self._volumeops.get_hosts().AndReturn(retrieve_result)
        m.StubOutWithMock(self._driver, '_create_backing')
        volume = FakeObject()
        volume['name'] = 'vol_name'
        backing = FakeMor('VirtualMachine', 'my_back')
        mux = self._driver._create_backing(volume, host1.obj)
        mux.AndRaise(error_util.VimException('Maintenance mode'))
        mux = self._driver._create_backing(volume, host2.obj)
        mux.AndReturn(backing)
        m.StubOutWithMock(self._volumeops, 'cancel_retrieval')
        self._volumeops.cancel_retrieval(retrieve_result)
        m.StubOutWithMock(self._volumeops, 'continue_retrieval')

        m.ReplayAll()
        result = self._driver._create_backing_in_inventory(volume)
        self.assertEqual(result, backing)
        m.UnsetStubs()
        m.VerifyAll()

    def test_init_conn_with_instance_and_backing(self):
        """Test initialize_connection with instance and backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        volume = FakeObject()
        volume['name'] = 'volume_name'
        volume['id'] = 'volume_id'
        volume['size'] = 1
        connector = {'instance': 'my_instance'}
        backing = FakeMor('VirtualMachine', 'my_back')
        self._volumeops.get_backing(volume['name']).AndReturn(backing)
        m.StubOutWithMock(self._volumeops, 'get_host')
        host = FakeMor('HostSystem', 'my_host')
        self._volumeops.get_host(mox.IgnoreArg()).AndReturn(host)

        m.ReplayAll()
        conn_info = self._driver.initialize_connection(volume, connector)
        self.assertEqual(conn_info['driver_volume_type'], 'vmdk')
        self.assertEqual(conn_info['data']['volume'], 'my_back')
        self.assertEqual(conn_info['data']['volume_id'], 'volume_id')
        m.UnsetStubs()
        m.VerifyAll()

    def test_get_volume_group_folder(self):
        """Test _get_volume_group_folder."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        datacenter = FakeMor('Datacenter', 'my_dc')
        m.StubOutWithMock(self._volumeops, 'get_vmfolder')
        self._volumeops.get_vmfolder(datacenter)

        m.ReplayAll()
        self._driver._get_volume_group_folder(datacenter)
        m.UnsetStubs()
        m.VerifyAll()

    def test_select_datastore_summary(self):
        """Test _select_datastore_summary."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops

        datastore1 = FakeMor('Datastore', 'my_ds_1')
        datastore2 = FakeMor('Datastore', 'my_ds_2')
        datastore3 = FakeMor('Datastore', 'my_ds_3')
        datastore4 = FakeMor('Datastore', 'my_ds_4')
        datastores = [datastore1, datastore2, datastore3, datastore4]

        m.StubOutWithMock(self._volumeops, 'get_summary')
        summary1 = FakeDatastoreSummary(5, 100)
        summary2 = FakeDatastoreSummary(25, 100)
        summary3 = FakeDatastoreSummary(50, 100)
        summary4 = FakeDatastoreSummary(75, 100)

        self._volumeops.get_summary(
            datastore1).MultipleTimes().AndReturn(summary1)
        self._volumeops.get_summary(
            datastore2).MultipleTimes().AndReturn(summary2)
        self._volumeops.get_summary(
            datastore3).MultipleTimes().AndReturn(summary3)
        self._volumeops.get_summary(
            datastore4).MultipleTimes().AndReturn(summary4)

        m.StubOutWithMock(self._volumeops, 'get_connected_hosts')

        host1 = FakeMor('HostSystem', 'my_host_1')
        host2 = FakeMor('HostSystem', 'my_host_2')
        host3 = FakeMor('HostSystem', 'my_host_3')
        host4 = FakeMor('HostSystem', 'my_host_4')

        self._volumeops.get_connected_hosts(
            datastore1).MultipleTimes().AndReturn([host1, host2, host3, host4])
        self._volumeops.get_connected_hosts(
            datastore2).MultipleTimes().AndReturn([host1, host2, host3])
        self._volumeops.get_connected_hosts(
            datastore3).MultipleTimes().AndReturn([host1, host2])
        self._volumeops.get_connected_hosts(
            datastore4).MultipleTimes().AndReturn([host1, host2])

        m.ReplayAll()

        summary = self._driver._select_datastore_summary(1, datastores)
        self.assertEqual(summary, summary1)

        summary = self._driver._select_datastore_summary(10, datastores)
        self.assertEqual(summary, summary2)

        summary = self._driver._select_datastore_summary(40, datastores)
        self.assertEqual(summary, summary4)

        self.assertRaises(error_util.VimException,
                          self._driver._select_datastore_summary,
                          100, datastores)
        m.UnsetStubs()
        m.VerifyAll()

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareEsxVmdkDriver.'
                'session', new_callable=mock.PropertyMock)
    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareEsxVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_get_folder_ds_summary(self, volumeops, session):
        """Test _get_folder_ds_summary."""
        volumeops = volumeops.return_value
        driver = self._driver
        volume = {'size': 10, 'volume_type_id': 'fake_type'}
        rp = mock.sentinel.resource_pool
        dss = mock.sentinel.datastores
        # patch method calls from _get_folder_ds_summary
        volumeops.get_dc.return_value = mock.sentinel.dc
        volumeops.get_vmfolder.return_value = mock.sentinel.folder
        driver._get_storage_profile = mock.MagicMock()
        driver._select_datastore_summary = mock.MagicMock()
        driver._select_datastore_summary.return_value = mock.sentinel.summary
        # call _get_folder_ds_summary
        (folder, datastore_summary) = driver._get_folder_ds_summary(volume,
                                                                    rp, dss)
        # verify returned values and calls made
        self.assertEqual(mock.sentinel.folder, folder,
                         "Folder returned is wrong.")
        self.assertEqual(mock.sentinel.summary, datastore_summary,
                         "Datastore summary returned is wrong.")
        volumeops.get_dc.assert_called_once_with(rp)
        volumeops.get_vmfolder.assert_called_once_with(mock.sentinel.dc)
        driver._get_storage_profile.assert_called_once_with(volume)
        size = volume['size'] * units.GiB
        driver._select_datastore_summary.assert_called_once_with(size, dss)

    def test_get_disk_type(self):
        """Test _get_disk_type."""
        volume = FakeObject()
        volume['volume_type_id'] = None
        self.assertEqual(vmdk.VMwareEsxVmdkDriver._get_disk_type(volume),
                         'thin')

    def test_init_conn_with_instance_no_backing(self):
        """Test initialize_connection with instance and without backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        volume = FakeObject()
        volume['name'] = 'volume_name'
        volume['id'] = 'volume_id'
        volume['size'] = 1
        volume['volume_type_id'] = None
        connector = {'instance': 'my_instance'}
        self._volumeops.get_backing(volume['name'])
        m.StubOutWithMock(self._volumeops, 'get_host')
        host = FakeMor('HostSystem', 'my_host')
        self._volumeops.get_host(mox.IgnoreArg()).AndReturn(host)
        m.StubOutWithMock(self._volumeops, 'get_dss_rp')
        resource_pool = FakeMor('ResourcePool', 'my_rp')
        datastores = [FakeMor('Datastore', 'my_ds')]
        self._volumeops.get_dss_rp(host).AndReturn((datastores, resource_pool))
        m.StubOutWithMock(self._driver, '_get_folder_ds_summary')
        folder = FakeMor('Folder', 'my_fol')
        summary = FakeDatastoreSummary(1, 1)
        self._driver._get_folder_ds_summary(volume, resource_pool,
                                            datastores).AndReturn((folder,
                                                                   summary))
        backing = FakeMor('VirtualMachine', 'my_back')
        m.StubOutWithMock(self._volumeops, 'create_backing')
        self._volumeops.create_backing(volume['name'],
                                       volume['size'] * units.MiB,
                                       mox.IgnoreArg(), folder,
                                       resource_pool, host,
                                       mox.IgnoreArg(),
                                       mox.IgnoreArg()).AndReturn(backing)

        m.ReplayAll()
        conn_info = self._driver.initialize_connection(volume, connector)
        self.assertEqual(conn_info['driver_volume_type'], 'vmdk')
        self.assertEqual(conn_info['data']['volume'], 'my_back')
        self.assertEqual(conn_info['data']['volume_id'], 'volume_id')
        m.UnsetStubs()
        m.VerifyAll()

    def test_init_conn_without_instance(self):
        """Test initialize_connection without instance and a backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        backing = FakeMor('VirtualMachine', 'my_back')
        volume = FakeObject()
        volume['name'] = 'volume_name'
        volume['id'] = 'volume_id'
        connector = {}
        self._volumeops.get_backing(volume['name']).AndReturn(backing)

        m.ReplayAll()
        conn_info = self._driver.initialize_connection(volume, connector)
        self.assertEqual(conn_info['driver_volume_type'], 'vmdk')
        self.assertEqual(conn_info['data']['volume'], 'my_back')
        self.assertEqual(conn_info['data']['volume_id'], 'volume_id')
        m.UnsetStubs()
        m.VerifyAll()

    def test_create_snapshot_without_backing(self):
        """Test vmdk.create_snapshot without backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        snapshot = FakeObject()
        snapshot['volume_name'] = 'volume_name'
        snapshot['name'] = 'snap_name'
        snapshot['volume'] = FakeObject()
        snapshot['volume']['status'] = 'available'
        self._volumeops.get_backing(snapshot['volume_name'])

        m.ReplayAll()
        self._driver.create_snapshot(snapshot)
        m.UnsetStubs()
        m.VerifyAll()

    def test_create_snapshot_with_backing(self):
        """Test vmdk.create_snapshot with backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        snapshot = FakeObject()
        snapshot['volume_name'] = 'volume_name'
        snapshot['name'] = 'snapshot_name'
        snapshot['display_description'] = 'snapshot_desc'
        snapshot['volume'] = FakeObject()
        snapshot['volume']['status'] = 'available'
        backing = FakeMor('VirtualMachine', 'my_back')
        self._volumeops.get_backing(snapshot['volume_name']).AndReturn(backing)
        m.StubOutWithMock(self._volumeops, 'create_snapshot')
        self._volumeops.create_snapshot(backing, snapshot['name'],
                                        snapshot['display_description'])

        m.ReplayAll()
        self._driver.create_snapshot(snapshot)
        m.UnsetStubs()
        m.VerifyAll()

    def test_create_snapshot_when_attached(self):
        """Test vmdk.create_snapshot when volume is attached."""
        snapshot = FakeObject()
        snapshot['volume'] = FakeObject()
        snapshot['volume']['status'] = 'in-use'

        self.assertRaises(exception.InvalidVolume,
                          self._driver.create_snapshot, snapshot)

    def test_delete_snapshot_without_backing(self):
        """Test delete_snapshot without backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        snapshot = FakeObject()
        snapshot['volume_name'] = 'volume_name'
        snapshot['name'] = 'snap_name'
        snapshot['volume'] = FakeObject()
        snapshot['volume']['status'] = 'available'
        self._volumeops.get_backing(snapshot['volume_name'])

        m.ReplayAll()
        self._driver.delete_snapshot(snapshot)
        m.UnsetStubs()
        m.VerifyAll()

    def test_delete_snapshot_with_backing(self):
        """Test delete_snapshot with backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        snapshot = FakeObject()
        snapshot['name'] = 'snapshot_name'
        snapshot['volume_name'] = 'volume_name'
        snapshot['name'] = 'snap_name'
        snapshot['volume'] = FakeObject()
        snapshot['volume']['status'] = 'available'
        backing = FakeMor('VirtualMachine', 'my_back')
        self._volumeops.get_backing(snapshot['volume_name']).AndReturn(backing)
        m.StubOutWithMock(self._volumeops, 'delete_snapshot')
        self._volumeops.delete_snapshot(backing,
                                        snapshot['name'])

        m.ReplayAll()
        self._driver.delete_snapshot(snapshot)
        m.UnsetStubs()
        m.VerifyAll()

    def test_delete_snapshot_when_attached(self):
        """Test delete_snapshot when volume is attached."""
        snapshot = FakeObject()
        snapshot['volume'] = FakeObject()
        snapshot['volume']['status'] = 'in-use'

        self.assertRaises(exception.InvalidVolume,
                          self._driver.delete_snapshot, snapshot)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareEsxVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_cloned_volume_without_backing(self, mock_vops):
        """Test create_cloned_volume without a backing."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'name': 'mock_vol'}
        src_vref = {'name': 'src_snapshot_name'}
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = None

        # invoke the create_volume_from_snapshot api
        driver.create_cloned_volume(volume, src_vref)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('src_snapshot_name')

    def test_clone_backing_by_copying(self):
        """Test _clone_backing_by_copying."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        volume = FakeObject()
        src_vmdk_path = "[datastore] src_vm/src_vm.vmdk"
        new_vmdk_path = "[datastore] dest_vm/dest_vm.vmdk"
        backing = FakeMor('VirtualMachine', 'my_back')
        m.StubOutWithMock(self._driver, '_create_backing_in_inventory')
        mux = self._driver._create_backing_in_inventory(volume)
        mux.AndReturn(backing)
        m.StubOutWithMock(self._volumeops, 'get_vmdk_path')
        self._volumeops.get_vmdk_path(backing).AndReturn(new_vmdk_path)
        m.StubOutWithMock(self._volumeops, 'get_dc')
        datacenter = FakeMor('Datacenter', 'my_dc')
        self._volumeops.get_dc(backing).AndReturn(datacenter)
        m.StubOutWithMock(self._volumeops, 'delete_vmdk_file')
        self._volumeops.delete_vmdk_file(new_vmdk_path, datacenter)
        m.StubOutWithMock(self._volumeops, 'copy_vmdk_file')
        self._volumeops.copy_vmdk_file(datacenter, src_vmdk_path,
                                       new_vmdk_path)

        m.ReplayAll()
        self._driver._clone_backing_by_copying(volume, src_vmdk_path)
        m.UnsetStubs()
        m.VerifyAll()

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareEsxVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_cloned_volume_with_backing(self, mock_vops):
        """Test create_cloned_volume with a backing."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = mock.sentinel.volume
        src_vref = {'name': 'src_snapshot_name'}
        backing = mock.sentinel.backing
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        src_vmdk_path = "[datastore] src_vm/src_vm.vmdk"
        mock_vops.get_vmdk_path.return_value = src_vmdk_path
        driver._clone_backing_by_copying = mock.MagicMock()

        # invoke the create_volume_from_snapshot api
        driver.create_cloned_volume(volume, src_vref)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('src_snapshot_name')
        mock_vops.get_vmdk_path.assert_called_once_with(backing)
        driver._clone_backing_by_copying.assert_called_once_with(volume,
                                                                 src_vmdk_path)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareEsxVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_volume_from_snapshot_without_backing(self, mock_vops):
        """Test create_volume_from_snapshot without a backing."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'name': 'mock_vol'}
        snapshot = {'volume_name': 'mock_vol', 'name': 'mock_snap'}
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = None

        # invoke the create_volume_from_snapshot api
        driver.create_volume_from_snapshot(volume, snapshot)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('mock_vol')

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareEsxVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_volume_from_snap_without_backing_snap(self, mock_vops):
        """Test create_volume_from_snapshot without a backing snapshot."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'volume_type_id': None, 'name': 'mock_vol'}
        snapshot = {'volume_name': 'mock_vol', 'name': 'mock_snap'}
        backing = mock.sentinel.backing
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        mock_vops.get_snapshot.return_value = None

        # invoke the create_volume_from_snapshot api
        driver.create_volume_from_snapshot(volume, snapshot)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('mock_vol')
        mock_vops.get_snapshot.assert_called_once_with(backing,
                                                       'mock_snap')

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareEsxVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_volume_from_snapshot(self, mock_vops):
        """Test create_volume_from_snapshot."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'volume_type_id': None, 'name': 'mock_vol'}
        snapshot = {'volume_name': 'mock_vol', 'name': 'mock_snap'}
        backing = mock.sentinel.backing
        snap_moref = mock.sentinel.snap_moref
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        mock_vops.get_snapshot.return_value = snap_moref
        src_vmdk_path = "[datastore] src_vm/src_vm-001.vmdk"
        mock_vops.get_vmdk_path.return_value = src_vmdk_path
        driver._clone_backing_by_copying = mock.MagicMock()

        # invoke the create_volume_from_snapshot api
        driver.create_volume_from_snapshot(volume, snapshot)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('mock_vol')
        mock_vops.get_snapshot.assert_called_once_with(backing,
                                                       'mock_snap')
        mock_vops.get_vmdk_path.assert_called_once_with(snap_moref)
        driver._clone_backing_by_copying.assert_called_once_with(volume,
                                                                 src_vmdk_path)

    def test_copy_image_to_volume_non_vmdk(self):
        """Test copy_image_to_volume for a non-vmdk disk format."""
        m = self.mox
        image_id = 'image-123456789'
        image_meta = FakeObject()
        image_meta['disk_format'] = 'novmdk'
        image_service = m.CreateMock(glance.GlanceImageService)
        image_service.show(mox.IgnoreArg(), image_id).AndReturn(image_meta)

        m.ReplayAll()
        self.assertRaises(exception.ImageUnacceptable,
                          self._driver.copy_image_to_volume,
                          mox.IgnoreArg(), mox.IgnoreArg(),
                          image_service, image_id)
        m.UnsetStubs()
        m.VerifyAll()

    def test_copy_image_to_volume_vmdk(self):
        """Test copy_image_to_volume with an acceptable vmdk disk format."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'session')
        self._driver.session = self._session
        m.StubOutWithMock(api.VMwareAPISession, 'vim')
        self._session.vim = self._vim
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops

        image_id = 'image-id'
        image_meta = FakeObject()
        image_meta['disk_format'] = 'vmdk'
        image_meta['size'] = 1 * units.MiB
        image_meta['properties'] = {'vmware_disktype': 'preallocated'}
        image_service = m.CreateMock(glance.GlanceImageService)
        image_service.show(mox.IgnoreArg(), image_id).AndReturn(image_meta)
        volume = FakeObject()
        vol_name = 'volume name'
        volume['name'] = vol_name
        backing = FakeMor('VirtualMachine', 'my_vm')
        m.StubOutWithMock(self._driver, '_create_backing_in_inventory')
        self._driver._create_backing_in_inventory(volume).AndReturn(backing)
        datastore_name = 'datastore1'
        flat_vmdk_path = 'myvolumes/myvm-flat.vmdk'
        m.StubOutWithMock(self._driver, '_get_ds_name_flat_vmdk_path')
        moxed = self._driver._get_ds_name_flat_vmdk_path(mox.IgnoreArg(),
                                                         vol_name)
        moxed.AndReturn((datastore_name, flat_vmdk_path))
        host = FakeMor('Host', 'my_host')
        m.StubOutWithMock(self._volumeops, 'get_host')
        self._volumeops.get_host(backing).AndReturn(host)
        datacenter = FakeMor('Datacenter', 'my_datacenter')
        m.StubOutWithMock(self._volumeops, 'get_dc')
        self._volumeops.get_dc(host).AndReturn(datacenter)
        datacenter_name = 'my-datacenter'
        m.StubOutWithMock(self._volumeops, 'get_entity_name')
        self._volumeops.get_entity_name(datacenter).AndReturn(datacenter_name)
        flat_path = '[%s] %s' % (datastore_name, flat_vmdk_path)
        m.StubOutWithMock(self._volumeops, 'delete_file')
        self._volumeops.delete_file(flat_path, datacenter)
        client = FakeObject()
        client.options = FakeObject()
        client.options.transport = FakeObject()
        cookies = FakeObject()
        client.options.transport.cookiejar = cookies
        m.StubOutWithMock(self._vim.__class__, 'client')
        self._vim.client = client
        m.StubOutWithMock(vmware_images, 'fetch_flat_image')
        timeout = self._config.vmware_image_transfer_timeout_secs
        vmware_images.fetch_flat_image(mox.IgnoreArg(), timeout, image_service,
                                       image_id, image_size=image_meta['size'],
                                       host=self.IP,
                                       data_center_name=datacenter_name,
                                       datastore_name=datastore_name,
                                       cookies=cookies,
                                       file_path=flat_vmdk_path)

        m.ReplayAll()
        self._driver.copy_image_to_volume(mox.IgnoreArg(), volume,
                                          image_service, image_id)
        m.UnsetStubs()
        m.VerifyAll()

    def test_copy_image_to_volume_stream_optimized(self):
        """Test copy_image_to_volume.

        Test with an acceptable vmdk disk format and streamOptimized disk type.
        """
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'session')
        self._driver.session = self._session
        m.StubOutWithMock(api.VMwareAPISession, 'vim')
        self._session.vim = self._vim
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops

        image_id = 'image-id'
        size = 5 * units.GiB
        size_kb = float(size) / units.KiB
        size_gb = float(size) / units.GiB
        # image_service.show call
        image_meta = FakeObject()
        image_meta['disk_format'] = 'vmdk'
        image_meta['size'] = size
        image_meta['properties'] = {'vmware_disktype': 'streamOptimized'}
        image_service = m.CreateMock(glance.GlanceImageService)
        image_service.show(mox.IgnoreArg(), image_id).AndReturn(image_meta)
        # _select_ds_for_volume call
        (host, rp, folder, summary) = (FakeObject(), FakeObject(),
                                       FakeObject(), FakeObject())
        summary.name = "datastore-1"
        vol_name = 'volume name'
        volume = FakeObject()
        volume['name'] = vol_name
        volume['size'] = size_gb
        volume['volume_type_id'] = None  # _get_disk_type will return 'thin'
        disk_type = 'thin'
        m.StubOutWithMock(self._driver, '_select_ds_for_volume')
        self._driver._select_ds_for_volume(volume).AndReturn((host, rp,
                                                              folder,
                                                              summary))

        # _get_create_spec call
        m.StubOutWithMock(self._volumeops, '_get_create_spec')
        self._volumeops._get_create_spec(vol_name, 0, disk_type,
                                         summary.name)

        # vim.client.factory.create call
        class FakeFactory(object):
            def create(self, name):
                return mox.MockAnything()

        client = FakeObject()
        client.factory = FakeFactory()
        m.StubOutWithMock(self._vim.__class__, 'client')
        self._vim.client = client
        # fetch_stream_optimized_image call
        timeout = self._config.vmware_image_transfer_timeout_secs
        m.StubOutWithMock(vmware_images, 'fetch_stream_optimized_image')
        vmware_images.fetch_stream_optimized_image(mox.IgnoreArg(), timeout,
                                                   image_service, image_id,
                                                   session=self._session,
                                                   host=self.IP,
                                                   resource_pool=rp,
                                                   vm_folder=folder,
                                                   vm_create_spec=
                                                   mox.IgnoreArg(),
                                                   image_size=size)

        m.ReplayAll()
        self._driver.copy_image_to_volume(mox.IgnoreArg(), volume,
                                          image_service, image_id)
        m.UnsetStubs()
        m.VerifyAll()

    def test_copy_volume_to_image_non_vmdk(self):
        """Test copy_volume_to_image for a non-vmdk disk format."""
        m = self.mox
        image_meta = FakeObject()
        image_meta['disk_format'] = 'novmdk'
        volume = FakeObject()
        volume['name'] = 'vol-name'
        volume['instance_uuid'] = None
        volume['attached_host'] = None

        m.ReplayAll()
        self.assertRaises(exception.ImageUnacceptable,
                          self._driver.copy_volume_to_image,
                          mox.IgnoreArg(), volume,
                          mox.IgnoreArg(), image_meta)
        m.UnsetStubs()
        m.VerifyAll()

    def test_copy_volume_to_image_when_attached(self):
        """Test copy_volume_to_image when volume is attached."""
        m = self.mox
        volume = FakeObject()
        volume['instance_uuid'] = 'my_uuid'

        m.ReplayAll()
        self.assertRaises(exception.InvalidVolume,
                          self._driver.copy_volume_to_image,
                          mox.IgnoreArg(), volume,
                          mox.IgnoreArg(), mox.IgnoreArg())
        m.UnsetStubs()
        m.VerifyAll()

    def test_copy_volume_to_image_vmdk(self):
        """Test copy_volume_to_image for a valid vmdk disk format."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'session')
        self._driver.session = self._session
        m.StubOutWithMock(api.VMwareAPISession, 'vim')
        self._session.vim = self._vim
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops

        image_id = 'image-id-1'
        image_meta = FakeObject()
        image_meta['disk_format'] = 'vmdk'
        image_meta['id'] = image_id
        image_meta['name'] = image_id
        image_service = FakeObject()
        vol_name = 'volume-123456789'
        project_id = 'project-owner-id-123'
        volume = FakeObject()
        volume['name'] = vol_name
        size_gb = 5
        size = size_gb * units.GiB
        volume['size'] = size_gb
        volume['project_id'] = project_id
        volume['instance_uuid'] = None
        volume['attached_host'] = None
        # volumeops.get_backing
        backing = FakeMor("VirtualMachine", "my_vm")
        m.StubOutWithMock(self._volumeops, 'get_backing')
        self._volumeops.get_backing(vol_name).AndReturn(backing)
        # volumeops.get_vmdk_path
        datastore_name = 'datastore1'
        file_path = 'my_folder/my_nested_folder/my_vm.vmdk'
        vmdk_file_path = '[%s] %s' % (datastore_name, file_path)
        m.StubOutWithMock(self._volumeops, 'get_vmdk_path')
        self._volumeops.get_vmdk_path(backing).AndReturn(vmdk_file_path)
        # vmware_images.upload_image
        timeout = self._config.vmware_image_transfer_timeout_secs
        host_ip = self.IP
        m.StubOutWithMock(vmware_images, 'upload_image')
        vmware_images.upload_image(mox.IgnoreArg(), timeout, image_service,
                                   image_id, project_id, session=self._session,
                                   host=host_ip, vm=backing,
                                   vmdk_file_path=vmdk_file_path,
                                   vmdk_size=size,
                                   image_name=image_id,
                                   image_version=1)

        m.ReplayAll()
        self._driver.copy_volume_to_image(mox.IgnoreArg(), volume,
                                          image_service, image_meta)
        m.UnsetStubs()
        m.VerifyAll()

    def test_retrieve_properties_ex_fault_checker(self):
        """Test retrieve_properties_ex_fault_checker is called."""
        m = self.mox

        class FakeVim(vim.Vim):
            def __init__(self):
                pass

            @property
            def client(self):

                class FakeRetrv(object):
                    def RetrievePropertiesEx(self, collector):
                        pass

                    def __getattr__(self, name):
                        if name == 'service':
                            return FakeRetrv()

                return FakeRetrv()

            def RetrieveServiceContent(self, type='ServiceInstance'):
                return mox.MockAnything()

        _vim = FakeVim()
        m.ReplayAll()
        # retrieve_properties_ex_fault_checker throws authentication error
        self.assertRaises(error_util.VimFaultException,
                          _vim.RetrievePropertiesEx, mox.IgnoreArg())
        m.UnsetStubs()
        m.VerifyAll()


class VMwareVcVmdkDriverTestCase(VMwareEsxVmdkDriverTestCase):
    """Test class for VMwareVcVmdkDriver."""

    PBM_WSDL = '/fake/wsdl/path'
    DEFAULT_PROFILE = 'fakeProfile'

    def setUp(self):
        super(VMwareVcVmdkDriverTestCase, self).setUp()
        self._config.pbm_wsdl_location = self.PBM_WSDL
        self._config.pbm_default_policy = self.DEFAULT_PROFILE
        self._driver = vmdk.VMwareVcVmdkDriver(configuration=self._config)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'session', new_callable=mock.PropertyMock)
    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_do_setup(self, vol_ops, session):
        """Test do_setup."""
        vol_ops = vol_ops.return_value
        session = session.return_value
        # pbm_wsdl_location is set and pbm_default_policy is used
        self._driver.do_setup(mock.ANY)
        default = self.DEFAULT_PROFILE
        vol_ops.retrieve_profile_id.assert_called_once_with(default)
        # pbm_wsdl_location is set and pbm_default_policy is wrong
        vol_ops.retrieve_profile_id.return_value = None
        self.assertRaises(error_util.PbmDefaultPolicyDoesNotExist,
                          self._driver.do_setup, mock.ANY)
        # pbm_wsdl_location is not set
        self._driver.configuration.pbm_wsdl_location = None
        self._driver.do_setup(mock.ANY)

    def test_init_conn_with_instance_and_backing(self):
        """Test initialize_connection with instance and backing."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        volume = FakeObject()
        volume['name'] = 'volume_name'
        volume['id'] = 'volume_id'
        volume['size'] = 1
        connector = {'instance': 'my_instance'}
        backing = FakeMor('VirtualMachine', 'my_back')
        self._volumeops.get_backing(volume['name']).AndReturn(backing)
        m.StubOutWithMock(self._volumeops, 'get_host')
        host = FakeMor('HostSystem', 'my_host')
        self._volumeops.get_host(mox.IgnoreArg()).AndReturn(host)
        datastore = FakeMor('Datastore', 'my_ds')
        resource_pool = FakeMor('ResourcePool', 'my_rp')
        m.StubOutWithMock(self._volumeops, 'get_dss_rp')
        self._volumeops.get_dss_rp(host).AndReturn(([datastore],
                                                    resource_pool))
        m.StubOutWithMock(self._volumeops, 'get_datastore')
        self._volumeops.get_datastore(backing).AndReturn(datastore)

        m.ReplayAll()
        conn_info = self._driver.initialize_connection(volume, connector)
        self.assertEqual(conn_info['driver_volume_type'], 'vmdk')
        self.assertEqual(conn_info['data']['volume'], 'my_back')
        self.assertEqual(conn_info['data']['volume_id'], 'volume_id')
        m.UnsetStubs()
        m.VerifyAll()

    def test_get_volume_group_folder(self):
        """Test _get_volume_group_folder."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        datacenter = FakeMor('Datacenter', 'my_dc')
        m.StubOutWithMock(self._volumeops, 'get_vmfolder')
        self._volumeops.get_vmfolder(datacenter)
        m.StubOutWithMock(self._volumeops, 'create_folder')
        self._volumeops.create_folder(mox.IgnoreArg(),
                                      self._config.vmware_volume_folder)

        m.ReplayAll()
        self._driver._get_volume_group_folder(datacenter)
        m.UnsetStubs()
        m.VerifyAll()

    def test_init_conn_with_instance_and_backing_and_relocation(self):
        """Test initialize_connection with backing being relocated."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'get_backing')
        volume = FakeObject()
        volume['name'] = 'volume_name'
        volume['id'] = 'volume_id'
        volume['size'] = 1
        connector = {'instance': 'my_instance'}
        backing = FakeMor('VirtualMachine', 'my_back')
        self._volumeops.get_backing(volume['name']).AndReturn(backing)
        m.StubOutWithMock(self._volumeops, 'get_host')
        host = FakeMor('HostSystem', 'my_host')
        self._volumeops.get_host(mox.IgnoreArg()).AndReturn(host)
        datastore1 = FakeMor('Datastore', 'my_ds_1')
        datastore2 = FakeMor('Datastore', 'my_ds_2')
        resource_pool = FakeMor('ResourcePool', 'my_rp')
        m.StubOutWithMock(self._volumeops, 'get_dss_rp')
        self._volumeops.get_dss_rp(host).AndReturn(([datastore1],
                                                    resource_pool))
        m.StubOutWithMock(self._volumeops, 'get_datastore')
        self._volumeops.get_datastore(backing).AndReturn(datastore2)
        m.StubOutWithMock(self._driver, '_get_folder_ds_summary')
        folder = FakeMor('Folder', 'my_fol')
        summary = FakeDatastoreSummary(1, 1, datastore1)
        size = 1
        self._driver._get_folder_ds_summary(volume, resource_pool,
                                            [datastore1]).AndReturn((folder,
                                                                     summary))
        m.StubOutWithMock(self._volumeops, 'relocate_backing')
        self._volumeops.relocate_backing(backing, datastore1,
                                         resource_pool, host)
        m.StubOutWithMock(self._volumeops, 'move_backing_to_folder')
        self._volumeops.move_backing_to_folder(backing, folder)

        m.ReplayAll()
        conn_info = self._driver.initialize_connection(volume, connector)
        self.assertEqual(conn_info['driver_volume_type'], 'vmdk')
        self.assertEqual(conn_info['data']['volume'], 'my_back')
        self.assertEqual(conn_info['data']['volume_id'], 'volume_id')
        m.UnsetStubs()
        m.VerifyAll()

    def test_clone_backing_linked(self):
        """Test _clone_backing with clone type - linked."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        m.StubOutWithMock(self._volumeops, 'clone_backing')
        volume = FakeObject()
        volume['name'] = 'volume_name'
        self._volumeops.clone_backing(volume['name'], mox.IgnoreArg(),
                                      mox.IgnoreArg(),
                                      volumeops.LINKED_CLONE_TYPE,
                                      mox.IgnoreArg())

        m.ReplayAll()
        self._driver._clone_backing(volume, mox.IgnoreArg(), mox.IgnoreArg(),
                                    volumeops.LINKED_CLONE_TYPE)
        m.UnsetStubs()
        m.VerifyAll()

    def test_clone_backing_full(self):
        """Test _clone_backing with clone type - full."""
        m = self.mox
        m.StubOutWithMock(self._driver.__class__, 'volumeops')
        self._driver.volumeops = self._volumeops
        backing = FakeMor('VirtualMachine', 'my_vm')
        datastore = FakeMor('Datastore', 'my_ds')
        m.StubOutWithMock(self._driver, '_select_ds_for_volume')
        volume = FakeObject()
        volume['name'] = 'volume_name'
        volume['size'] = 1
        summary = FakeDatastoreSummary(1, 1, datastore=datastore)
        self._driver._select_ds_for_volume(volume).AndReturn((_, _, _,
                                                              summary))
        m.StubOutWithMock(self._volumeops, 'clone_backing')
        self._volumeops.clone_backing(volume['name'], backing,
                                      mox.IgnoreArg(),
                                      volumeops.FULL_CLONE_TYPE,
                                      datastore)

        m.ReplayAll()
        self._driver._clone_backing(volume, backing, mox.IgnoreArg(),
                                    volumeops.FULL_CLONE_TYPE)
        m.UnsetStubs()
        m.VerifyAll()

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_volume_from_snapshot_without_backing(self, mock_vops):
        """Test create_volume_from_snapshot without a backing."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'name': 'mock_vol'}
        snapshot = {'volume_name': 'mock_vol', 'name': 'mock_snap'}
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = None

        # invoke the create_volume_from_snapshot api
        driver.create_volume_from_snapshot(volume, snapshot)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('mock_vol')

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_volume_from_snap_without_backing_snap(self, mock_vops):
        """Test create_volume_from_snapshot without a backing snapshot."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'volume_type_id': None, 'name': 'mock_vol'}
        snapshot = {'volume_name': 'mock_vol', 'name': 'mock_snap'}
        backing = mock.sentinel.backing
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        mock_vops.get_snapshot.return_value = None

        # invoke the create_volume_from_snapshot api
        driver.create_volume_from_snapshot(volume, snapshot)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('mock_vol')
        mock_vops.get_snapshot.assert_called_once_with(backing,
                                                       'mock_snap')

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_volume_from_snapshot(self, mock_vops):
        """Test create_volume_from_snapshot."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'volume_type_id': None, 'name': 'mock_vol'}
        snapshot = {'volume_name': 'mock_vol', 'name': 'mock_snap'}
        backing = mock.sentinel.backing
        snap_moref = mock.sentinel.snap_moref
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        mock_vops.get_snapshot.return_value = snap_moref
        driver._clone_backing = mock.MagicMock()

        # invoke the create_volume_from_snapshot api
        driver.create_volume_from_snapshot(volume, snapshot)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('mock_vol')
        mock_vops.get_snapshot.assert_called_once_with(backing,
                                                       'mock_snap')
        default_clone_type = volumeops.FULL_CLONE_TYPE
        driver._clone_backing.assert_called_once_with(volume,
                                                      backing,
                                                      snap_moref,
                                                      default_clone_type)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_cloned_volume_without_backing(self, mock_vops):
        """Test create_cloned_volume without a backing."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'name': 'mock_vol'}
        src_vref = {'name': 'src_snapshot_name'}
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = None

        # invoke the create_volume_from_snapshot api
        driver.create_cloned_volume(volume, src_vref)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_create_cloned_volume_with_backing(self, mock_vops):
        """Test create_cloned_volume with clone type - full."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'volume_type_id': None, 'name': 'mock_vol'}
        src_vref = {'name': 'src_snapshot_name'}
        backing = mock.sentinel.backing
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        default_clone_type = volumeops.FULL_CLONE_TYPE
        driver._clone_backing = mock.MagicMock()

        # invoke the create_volume_from_snapshot api
        driver.create_cloned_volume(volume, src_vref)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('src_snapshot_name')
        driver._clone_backing.assert_called_once_with(volume,
                                                      backing,
                                                      None,
                                                      default_clone_type)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                '_get_clone_type')
    def test_create_linked_cloned_volume_with_backing(self, get_clone_type,
                                                      mock_vops):
        """Test create_cloned_volume with clone type - linked."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'volume_type_id': None, 'name': 'mock_vol', 'id': 'mock_id'}
        src_vref = {'name': 'src_snapshot_name', 'status': 'available'}
        backing = mock.sentinel.backing
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        linked_clone = volumeops.LINKED_CLONE_TYPE
        get_clone_type.return_value = linked_clone
        driver._clone_backing = mock.MagicMock()
        mock_vops.create_snapshot = mock.MagicMock()
        mock_vops.create_snapshot.return_value = mock.sentinel.snapshot

        # invoke the create_volume_from_snapshot api
        driver.create_cloned_volume(volume, src_vref)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('src_snapshot_name')
        get_clone_type.assert_called_once_with(volume)
        name = 'snapshot-%s' % volume['id']
        mock_vops.create_snapshot.assert_called_once_with(backing, name, None)
        driver._clone_backing.assert_called_once_with(volume,
                                                      backing,
                                                      mock.sentinel.snapshot,
                                                      linked_clone)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                '_get_clone_type')
    def test_create_linked_cloned_volume_when_attached(self, get_clone_type,
                                                       mock_vops):
        """Test create_cloned_volume linked clone when volume is attached."""
        mock_vops = mock_vops.return_value
        driver = self._driver
        volume = {'volume_type_id': None, 'name': 'mock_vol', 'id': 'mock_id'}
        src_vref = {'name': 'src_snapshot_name', 'status': 'in-use'}
        backing = mock.sentinel.backing
        driver._verify_volume_creation = mock.MagicMock()
        mock_vops.get_backing.return_value = backing
        linked_clone = volumeops.LINKED_CLONE_TYPE
        get_clone_type.return_value = linked_clone

        # invoke the create_volume_from_snapshot api
        self.assertRaises(exception.InvalidVolume,
                          driver.create_cloned_volume,
                          volume,
                          src_vref)

        # verify calls
        driver._verify_volume_creation.assert_called_once_with(volume)
        mock_vops.get_backing.assert_called_once_with('src_snapshot_name')
        get_clone_type.assert_called_once_with(volume)

    @mock.patch('cinder.volume.volume_types.get_volume_type_extra_specs')
    def test_get_storage_profile(self, get_volume_type_extra_specs):
        """Test vmdk _get_storage_profile."""

        # volume with no type id returns None
        volume = FakeObject()
        volume['volume_type_id'] = None
        sp = self._driver._get_storage_profile(volume)
        self.assertEqual(None, sp, "Without a volume_type_id no storage "
                         "profile should be returned.")

        # profile associated with the volume type should be returned
        fake_id = 'fake_volume_id'
        volume['volume_type_id'] = fake_id
        get_volume_type_extra_specs.return_value = 'fake_profile'
        profile = self._driver._get_storage_profile(volume)
        self.assertEqual('fake_profile', profile)
        spec_key = 'vmware:storage_profile'
        get_volume_type_extra_specs.assert_called_once_with(fake_id, spec_key)

        # default profile should be returned when no storage profile is
        # associated with the volume type
        get_volume_type_extra_specs.return_value = False
        profile = self._driver._get_storage_profile(volume)
        self.assertEqual(self.DEFAULT_PROFILE, profile)

    @mock.patch('cinder.volume.drivers.vmware.vim_util.'
                'convert_datastores_to_hubs')
    @mock.patch('cinder.volume.drivers.vmware.vim_util.'
                'convert_hubs_to_datastores')
    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'session', new_callable=mock.PropertyMock)
    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_filter_ds_by_profile(self, volumeops, session, hubs_to_ds,
                                  ds_to_hubs):
        """Test vmdk _filter_ds_by_profile() method."""

        volumeops = volumeops.return_value
        session = session.return_value

        # Test with no profile id
        datastores = [mock.sentinel.ds1, mock.sentinel.ds2]
        profile = 'fake_profile'
        volumeops.retrieve_profile_id.return_value = None
        self.assertRaises(error_util.VimException,
                          self._driver._filter_ds_by_profile,
                          datastores, profile)
        volumeops.retrieve_profile_id.assert_called_once_with(profile)

        # Test with a fake profile id
        profileId = 'fake_profile_id'
        filtered_dss = [mock.sentinel.ds1]
        # patch method calls from _filter_ds_by_profile
        volumeops.retrieve_profile_id.return_value = profileId
        pbm_cf = mock.sentinel.pbm_cf
        session.pbm.client.factory = pbm_cf
        hubs = [mock.sentinel.hub1, mock.sentinel.hub2]
        ds_to_hubs.return_value = hubs
        volumeops.filter_matching_hubs.return_value = mock.sentinel.hubs
        hubs_to_ds.return_value = filtered_dss
        # call _filter_ds_by_profile with a fake profile
        actual_dss = self._driver._filter_ds_by_profile(datastores, profile)
        # verify return value and called methods
        self.assertEqual(filtered_dss, actual_dss,
                         "Wrong filtered datastores returned.")
        ds_to_hubs.assert_called_once_with(pbm_cf, datastores)
        volumeops.filter_matching_hubs.assert_called_once_with(hubs,
                                                               profileId)
        hubs_to_ds.assert_called_once_with(mock.sentinel.hubs, datastores)

    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'session', new_callable=mock.PropertyMock)
    @mock.patch('cinder.volume.drivers.vmware.vmdk.VMwareVcVmdkDriver.'
                'volumeops', new_callable=mock.PropertyMock)
    def test_get_folder_ds_summary(self, volumeops, session):
        """Test _get_folder_ds_summary."""
        volumeops = volumeops.return_value
        driver = self._driver
        driver._storage_policy_enabled = True
        volume = {'size': 10, 'volume_type_id': 'fake_type'}
        rp = mock.sentinel.resource_pool
        dss = [mock.sentinel.datastore1, mock.sentinel.datastore2]
        filtered_dss = [mock.sentinel.datastore1]
        profile = mock.sentinel.profile

        def filter_ds(datastores, storage_profile):
            return filtered_dss

        # patch method calls from _get_folder_ds_summary
        volumeops.get_dc.return_value = mock.sentinel.dc
        volumeops.get_vmfolder.return_value = mock.sentinel.vmfolder
        volumeops.create_folder.return_value = mock.sentinel.folder
        driver._get_storage_profile = mock.MagicMock()
        driver._get_storage_profile.return_value = profile
        driver._filter_ds_by_profile = mock.MagicMock(side_effect=filter_ds)
        driver._select_datastore_summary = mock.MagicMock()
        driver._select_datastore_summary.return_value = mock.sentinel.summary
        # call _get_folder_ds_summary
        (folder, datastore_summary) = driver._get_folder_ds_summary(volume,
                                                                    rp, dss)
        # verify returned values and calls made
        self.assertEqual(mock.sentinel.folder, folder,
                         "Folder returned is wrong.")
        self.assertEqual(mock.sentinel.summary, datastore_summary,
                         "Datastore summary returned is wrong.")
        volumeops.get_dc.assert_called_once_with(rp)
        volumeops.get_vmfolder.assert_called_once_with(mock.sentinel.dc)
        volumeops.create_folder.assert_called_once_with(mock.sentinel.vmfolder,
                                                        self.VOLUME_FOLDER)
        driver._get_storage_profile.assert_called_once_with(volume)
        driver._filter_ds_by_profile.assert_called_once_with(dss, profile)
        size = volume['size'] * units.GiB
        driver._select_datastore_summary.assert_called_once_with(size,
                                                                 filtered_dss)
