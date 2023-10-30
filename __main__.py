import pulumi
from pulumi_aws import rds, ec2
import pulumi_random


def to_params(args):
    return [
        rds.ParameterGroupParameterArgs(name=k, value=v, apply_method="pending-reboot")
        for k, v in args.items()
    ]


cluster_parameter_group_args = {
    "pglogical.conflict_log_level": "error",
    "shared_preload_libraries": "pglogical",
    "rds.logical_replication": "1",
    # This is the default, but it needs to be at least more than the number of expected subscriptions
    # so I've called it out explicitly
    "max_replication_slots": "10",
    # As above, replication relevant - should be roughly: = cpu count
    "max_worker_processes": "8",
    # As above, replication relevant - should be at least: 1 + 2 * (subscriber count)
    "max_wal_senders": "10",
}

FAMILY = "aurora-postgresql12"
ENGINE = "aurora-postgresql"
ENGINE_VERSION = "12.12"
INSTANCE_CLASS = "db.t3.medium"
MASTER_USERNAME = "IupTestAdmin"


def make_cluster(name, cluster_parameter_group, password):
    cluster_name = f"{name}-{pulumi.get_stack()}"
    return rds.Cluster(
        f"{cluster_name}-cluster",
        cluster_identifier=f"{name}-{pulumi.get_stack()}",
        db_cluster_parameter_group_name=cluster_parameter_group.name,
        deletion_protection=False,  # This is a test cluster, deletion is fine
        engine=ENGINE,  # What we use in prod
        engine_mode="provisioned",  # We want a normal DB, not "serverless"
        engine_version=ENGINE_VERSION,  # What we use in prod
        master_password=password,  # Good enough for testing
        master_username=MASTER_USERNAME,  # Good enough for testing
        storage_encrypted=False,  # Test cluster, stored data is non-sensitive test data
        skip_final_snapshot=True,  # Test cluster, we want to be able to tear down at will
    )


def make_instances(name, cluster, count):
    instances = []
    for i in range(count):
        id = f"{name}-{pulumi.get_stack()}-instance-{i}"
        instance = rds.ClusterInstance(
            id,
            cluster_identifier=cluster.id,
            engine=ENGINE,
            engine_version=ENGINE_VERSION,
            identifier=id,
            instance_class=INSTANCE_CLASS,
            performance_insights_enabled=True,  # Probably important for measuring replication impact
            publicly_accessible=True,  # We want to be able to reach our test instances
        )
        instances.append(instance)


if pulumi.config.Config("clusters").require_bool("enabled"):
    cluster_parameter_group = rds.ClusterParameterGroup(
        "iup-replication-experiment-parameter-group",
        description="Parameter group for IUP replication experiment",
        family=FAMILY,
        parameters=to_params(cluster_parameter_group_args),
    )

    master_password = pulumi_random.RandomPassword(
        f"{pulumi.get_project()}-master-password",
        length=32,
        special=False,
    )

    cluster_one = make_cluster(
        "iup-replication-experiment-one",
        cluster_parameter_group,
        master_password.result,
    )
    cluster_two = make_cluster(
        "iup-replication-experiment-two",
        cluster_parameter_group,
        master_password.result,
    )

    instances_one = make_instances("iup-replication-experiment-one", cluster_one, 2)
    instances_two = make_instances("iup-replication-experiment-two", cluster_two, 2)

    pulumi.export("cluster_one", cluster_one)
    pulumi.export("cluster_two", cluster_two)
    pulumi.export("instances_one", instances_one)
    pulumi.export("instances_two", instances_two)
    pulumi.export("cluster_parameter_group", cluster_parameter_group)
    pulumi.export("master_password", master_password.result)

if pulumi.config.Config("compute").require_bool("enabled"):
    olivers_pub_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDqBrJAHEJAYAC2yEVFPFoeg1wy6i2QyPXR+8qJWvWbtq4YzzhhDPPyKeWgzL11d4lsRawIZWV/UjMghhJ66m9zG6LMFV5rX4WpOZYODeqcFejaktMutenXMnGiSiscXtzgv6ffEipR4mllI5T3EruYI8z9ROmjyXpQVFccxuex+LPwmO0wUf2OSFa7wK8v+B4Htb0gKeCKxJ13tcOOUL6e6BuwH7I0hrzgvMhHjVB4LSykRM6JdNro2MbT+piMsLC4MSBs3hweFmmrlKyTLAmyrk940d5QwgEVyvH/YiucHz/v0CAQYaObMiGigGS+/BRpSDV3/H2AYAN82JZQ02P35eo6fNzl+7lG8k6YGggG0gnCzbDIFf6kWADulxwg47t23pUuRhAAnZNfUbZ/x2Tte0akm0mVtFhln0dVsdmEloHgzWxED3vll7o/T7AAgMJjukapYPWe187YDPD0a/xYkdZ1F5lBZzGKOZ5rrpiOdme1C4LZQYPMztQmcbUBgd8oH9oSUI1RyHIKhuPzb4NBI8sGFxiNmanLLLtKBkFn6E4OixtkXMKgUR1DUMFINYu+WInROeNoZWVxvxNLl3HwSIYLklvhijigdKXdcoxd7xJaD5VSoolGo/iNkks+oc1HfM/KN+Fc66p+nNSBDkcq1OlTbr2FudwVeYJKjNxwnQ=="
    aws_safe_name_suffix = pulumi.get_stack().replace(".", "-")
    key_pair = ec2.KeyPair(
        f"olivers-key-pair={aws_safe_name_suffix}",
        key_name=f"olivers-key-pair-{aws_safe_name_suffix}",
        public_key=olivers_pub_key,
    )

    instance = ec2.Instance(
        f"iup-replication-experiment-test-client-{aws_safe_name_suffix}",
        ami="ami-0694d931cee176e7d",  # Ubuntu Server 22.04 LTS (HVM), SSD Volume Type
        instance_type="t3.medium",  # 2vCPU, 4GB RAM - you know, like a raspberry pi from a few years ago. Medium.
        key_name=key_pair.key_name,
        associate_public_ip_address=True,
    )

    pulumi.export("compute_key_pair", key_pair)
    pulumi.export("compute_instance", instance)
