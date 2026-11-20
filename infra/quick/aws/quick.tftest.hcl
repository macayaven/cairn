mock_provider "aws" {
  mock_data "aws_ami" {
    defaults = { id = "ami-00000000000000000" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_ec2_instance_type_offerings" {
    defaults = { locations = ["us-east-1-bos-1a", "us-east-1b", "us-east-1a"] }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      bucket = "cairn-quick-example"
      arn    = "arn:aws:s3:::cairn-quick-example"
    }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:cairn-quick-example-ABCDEF"
    }
  }
  mock_resource "aws_instance" {
    defaults = { id = "i-00000000000000000" }
  }
  mock_resource "aws_eip" {
    defaults = { id = "eipalloc-00000000000000000", public_ip = "192.0.2.10" }
  }
}

variables {
  region     = "us-east-1"
  domain     = "cairn.example.net"
  acme_email = "ops@example.net"
}

run "three_inputs_with_safe_defaults" {
  command = plan

  assert {
    condition = (
      can(regex("^ghcr\\.io/macayaven/cairn:[0-9]+\\.[0-9]+\\.[0-9]+$", var.image)) &&
      aws_instance.app.instance_type == "t3.small" &&
      one(aws_instance.app.root_block_device).volume_size == 20 &&
      one(aws_instance.app.root_block_device).encrypted
    )
    error_message = "The default VM must use a released image and an encrypted 20 GiB boot disk."
  }

  assert {
    condition     = aws_subnet.app.availability_zone == "us-east-1a"
    error_message = "The subnet goes in the first of the region's own zones that offers the instance type."
  }

  assert {
    condition = (
      toset([for rule in aws_vpc_security_group_ingress_rule.web : rule.from_port]) == toset([80, 443]) &&
      alltrue([for rule in aws_vpc_security_group_ingress_rule.web :
        rule.cidr_ipv4 == "0.0.0.0/0" && rule.ip_protocol == "tcp" && rule.from_port == rule.to_port
      ])
    )
    error_message = "Only TCP ports 80 and 443 should be open to the internet."
  }

  assert {
    condition = (
      one(aws_instance.app.metadata_options).http_endpoint == "enabled" &&
      one(aws_instance.app.metadata_options).http_tokens == "required" &&
      one(aws_instance.app.metadata_options).http_put_response_hop_limit == 2 &&
      one(aws_instance.app.metadata_options).http_protocol_ipv6 == "disabled"
    )
    error_message = "The backup bridge needs IPv4 IMDSv2 with a hop limit of two."
  }

  assert {
    condition = (
      aws_s3_bucket.replica.force_destroy == false &&
      aws_s3_bucket_public_access_block.replica.block_public_acls &&
      aws_s3_bucket_public_access_block.replica.block_public_policy &&
      aws_s3_bucket_public_access_block.replica.ignore_public_acls &&
      aws_s3_bucket_public_access_block.replica.restrict_public_buckets &&
      aws_s3_bucket_public_access_block.replica.skip_destroy &&
      one(one(aws_s3_bucket_server_side_encryption_configuration.replica.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    )
    error_message = "The replica bucket must be private, encrypted with SSE-S3 and never force-emptied."
  }

  assert {
    condition = alltrue([for filename in fileset(path.module, "*.tf") :
      length(regexall("(resource|data|ephemeral)\\s+\"(aws_iam_access_key|aws_secretsmanager_secret_version|aws_ssm_parameter)\"", file("${path.module}/${filename}"))) == 0
    ])
    error_message = "Only the secret's metadata belongs in OpenTofu. Do not create or look up key or secret values."
  }
}

run "a_region_where_no_zone_offers_the_type_is_refused" {
  command = plan

  override_data {
    target = data.aws_ec2_instance_type_offerings.app
    values = { locations = ["us-east-1-bos-1a"] }
  }

  expect_failures = [aws_subnet.app]
}

run "identity_is_scoped_to_the_replica_and_settings" {
  command = apply

  assert {
    condition = (
      toset([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Sid]) ==
      toset(["ReplicaBucket", "ReplicaObjects", "Settings"]) &&
      alltrue([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Effect == "Allow"]) &&
      one([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Resource if statement.Sid == "ReplicaBucket"]) == aws_s3_bucket.replica.arn &&
      one([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Resource if statement.Sid == "ReplicaObjects"]) == "${aws_s3_bucket.replica.arn}/cairn/*" &&
      one([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Resource if statement.Sid == "Settings"]) == aws_secretsmanager_secret.settings.arn
    )
    error_message = "The workload policy must be scoped to the one replica bucket, its cairn prefix and the one secret."
  }

  assert {
    condition = (
      toset(one([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Action if statement.Sid == "ReplicaBucket"])) == toset(["s3:GetBucketLocation", "s3:ListBucket"]) &&
      toset(one([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Action if statement.Sid == "ReplicaObjects"])) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]) &&
      toset(one([for statement in jsondecode(aws_iam_role_policy.app.policy).Statement : statement.Action if statement.Sid == "Settings"])) == toset(["secretsmanager:ListSecretVersionIds", "secretsmanager:GetSecretValue"]) &&
      aws_iam_role_policy_attachment.ssm.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    )
    error_message = "The VM gets the Litestream, settings-read and SSM permissions it needs."
  }

  assert {
    condition = (
      aws_instance.app.iam_instance_profile == aws_iam_instance_profile.app.name &&
      aws_iam_instance_profile.app.role == aws_iam_role.app.name &&
      aws_instance.app.user_data_replace_on_change &&
      output.connect_command == "aws ssm start-session --region us-east-1 --target ${aws_instance.app.id}" &&
      output.first_key_command == "sudo /opt/cairn/bootstrap.sh first-key"
    )
    error_message = "User-data replacement, VM identity and the documented operator commands must stay wired."
  }

  assert {
    condition = (
      aws_instance.app.associate_public_ip_address &&
      aws_eip_association.app.allocation_id == aws_eip.app.id &&
      aws_eip_association.app.instance_id == aws_instance.app.id &&
      output.public_ipv4 == aws_eip.app.public_ip
    )
    error_message = "The VM boots with a public address, and the DNS output is its reserved address."
  }
}

run "a_new_zone_listing_keeps_the_existing_subnet" {
  command = plan

  override_data {
    target = data.aws_ec2_instance_type_offerings.app
    values = { locations = ["us-east-1c"] }
  }

  assert {
    condition     = aws_subnet.app.availability_zone == "us-east-1a"
    error_message = "A change in where the instance type is offered does not move the subnet, and the VM with it."
  }
}

run "rolling_ami_keeps_the_existing_vm" {
  command = plan

  override_data {
    target = data.aws_ami.ubuntu
    values = { id = "ami-00000000000000001" }
  }

  assert {
    condition = (
      data.aws_ami.ubuntu.id == "ami-00000000000000001" &&
      aws_instance.app.ami == "ami-00000000000000000"
    )
    error_message = "A newer Canonical AMI does not replace an existing VM on a routine apply."
  }
}

run "explicit_replacement_uses_the_new_ami" {
  command = plan

  plan_options {
    replace = [aws_instance.app]
  }

  override_data {
    target = data.aws_ami.ubuntu
    values = { id = "ami-00000000000000001" }
  }

  assert {
    condition     = aws_instance.app.ami == "ami-00000000000000001"
    error_message = "An explicitly replaced VM uses the current Canonical AMI."
  }
}
