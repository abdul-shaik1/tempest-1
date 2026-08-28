import time

from oslo_log import log as logging

from tempest import config
from tempest import reporting
from tempest import tvaultconf
from tempest.api.workloadmgr import base
from tempest.lib import decorators

LOG = logging.getLogger(__name__)
CONF = config.CONF


class DMSMountTest(base.BaseWorkloadmgrTest):
    """
    TC-DMS-01: On-demand mount created for a snapshot job and
    auto-unmounted after job completion (Dynamic Mount Service).

    Verifies that the default (S3) backup target is not mounted while
    idle, gets mounted on-demand once a snapshot job needs it, and is
    auto-unmounted again once that job's reference on it is released.
    """
    credentials = ['primary']

    @decorators.attr(type='workloadmgr_api')
    def test_dms_s3_mount_lifecycle_for_snapshot_job(self):
        reporting.add_test_script(str(__name__) + "_s3_mount_lifecycle_api")
        try:
            # Resolve the default (S3) backup target's mount path from the
            # WLM API instead of hardcoding/decoding it.
            mount_path = self.get_mountpoint_path(tvaultconf.default_btt_id)
            LOG.debug(f"DMS S3 target mount_path under test: {mount_path}")

            vm_id = self.create_vm()
            server = self.servers_client.show_server(vm_id)['server']
            node_host = server['OS-EXT-SRV-ATTR:host']
            LOG.debug(f"Test VM {vm_id} scheduled on node: {node_host}")

            # Step 1: idle - target should not be mounted, no S3 FUSE process.
            mounted, running = self.get_dms_s3_mount_state(node_host, mount_path)
            LOG.debug(f"Before job: mounted={mounted}, running={running}")
            if not mounted and not running:
                reporting.add_test_step(
                    "Verify S3 target not mounted before job starts",
                    tvaultconf.PASS)
            else:
                reporting.add_test_step(
                    "Verify S3 target not mounted before job starts",
                    tvaultconf.FAIL)
                reporting.set_test_script_status(tvaultconf.FAIL)

            workload_id = self.workload_create([vm_id])
            snapshot_id = self.workload_snapshot(workload_id, is_full=True)

            # Step 2: job running - target should be mounted on-demand at
            # some point before the job completes. The job runs through
            # several phases (queued, metadata capture, then the actual
            # data transfer that needs the mount) asynchronously, so poll
            # alongside the snapshot's own status instead of checking once
            # after a fixed delay - a single early check can catch it
            # before the mount phase has started even though the job goes
            # on to mount it later.
            ever_mounted, ever_running = False, False
            timeout = 1800
            start_time = time.time()
            status = self.getSnapshotStatus(workload_id, snapshot_id)
            while status not in ('available', 'error'):
                mounted, running = self.get_dms_s3_mount_state(
                    node_host, mount_path)
                ever_mounted = ever_mounted or mounted
                ever_running = ever_running or running
                LOG.debug(f"During job (status={status}): mounted={mounted}, "
                         f"running={running}")
                if ever_mounted and ever_running:
                    break
                if time.time() - start_time > timeout:
                    LOG.error("Timeout waiting to observe DMS mount during job")
                    break
                time.sleep(15)
                status = self.getSnapshotStatus(workload_id, snapshot_id)

            if ever_mounted and ever_running:
                reporting.add_test_step(
                    "Verify S3 target mounted on-demand while job runs",
                    tvaultconf.PASS)
            else:
                reporting.add_test_step(
                    "Verify S3 target mounted on-demand while job runs",
                    tvaultconf.FAIL)
                reporting.set_test_script_status(tvaultconf.FAIL)

            self.wait_for_snapshot_tobe_available(workload_id, snapshot_id)

            # Step 3: job complete - target auto-unmounted (ref-count back
            # to 0).
            time.sleep(20)
            mounted, running = self.get_dms_s3_mount_state(node_host, mount_path)
            LOG.debug(f"After job: mounted={mounted}, running={running}")
            if not mounted and not running:
                reporting.add_test_step(
                    "Verify S3 target auto-unmounted after job completes",
                    tvaultconf.PASS)
            else:
                reporting.add_test_step(
                    "Verify S3 target auto-unmounted after job completes",
                    tvaultconf.FAIL)
                reporting.set_test_script_status(tvaultconf.FAIL)
        except Exception as e:
            LOG.error("Exception: " + str(e))
            reporting.add_test_step(str(e), tvaultconf.FAIL)
            reporting.set_test_script_status(tvaultconf.FAIL)
        finally:
            reporting.test_case_to_write()
