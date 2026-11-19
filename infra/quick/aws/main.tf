data "aws_partition" "current" {}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical in the commercial AWS partition.

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

locals {
  cloud_init = templatefile("${path.module}/../cloud-init.yaml.tftpl", {
    quick_dir        = "${path.module}/.."
    serve_extra_args = var.serve_extra_args
    config = {
      CAIRN_CLOUD            = "aws"
      CAIRN_DOMAIN           = var.domain
      ACME_EMAIL             = var.acme_email
      CAIRN_IMAGE            = var.image
      CAIRN_SETTINGS_SECRET  = aws_secretsmanager_secret.settings.arn
      AWS_REGION             = var.region
      LITESTREAM_REPLICA_URL = "s3://${aws_s3_bucket.replica.bucket}/cairn"
    }
  })
  user_data = base64gzip(local.cloud_init)
}

resource "aws_vpc" "app" {
  cidr_block           = "10.42.0.0/24"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "cairn-quick" }
}

resource "aws_subnet" "app" {
  vpc_id     = aws_vpc.app.id
  cidr_block = aws_vpc.app.cidr_block
}

resource "aws_internet_gateway" "app" {
  vpc_id = aws_vpc.app.id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.app.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.app.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.app.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "app" {
  name        = "cairn-quick"
  description = "cairn web access. Administration uses Session Manager."
  vpc_id      = aws_vpc.app.id
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each = toset(["80", "443"])

  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.key)
  to_port           = tonumber(each.key)
}

resource "aws_vpc_security_group_egress_rule" "app" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_s3_bucket" "replica" {
  bucket_prefix = "cairn-quick-"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "replica" {
  bucket                  = aws_s3_bucket.replica.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
  # Keep the block in place if destroy cannot delete a nonempty bucket.
  skip_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "replica" {
  bucket = aws_s3_bucket.replica.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_secretsmanager_secret" "settings" {
  name_prefix = "cairn-quick-env-"
  description = "cairn.env managed by the operator outside OpenTofu."
}

resource "aws_iam_role" "app" {
  name_prefix = "cairn-quick-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  name = "cairn-quick"
  role = aws_iam_role.app.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReplicaBucket"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket"]
        Resource = aws_s3_bucket.replica.arn
      },
      {
        Sid      = "ReplicaObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.replica.arn}/cairn/*"
      },
      {
        Sid      = "Settings"
        Effect   = "Allow"
        Action   = ["secretsmanager:ListSecretVersionIds", "secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.settings.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "app" {
  name_prefix = "cairn-quick-"
  role        = aws_iam_role.app.name
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = "t3.small"
  subnet_id              = aws_subnet.app.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name

  # Boot can reach the package repositories before the reserved address is attached.
  associate_public_ip_address = true
  user_data_base64            = local.user_data
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    http_protocol_ipv6          = "disabled"
  }

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  credit_specification {
    cpu_credits = "standard"
  }

  lifecycle {
    precondition {
      condition     = length(local.user_data) * 3 / 4 - length(regex("=*$", local.user_data)) <= 16384
      error_message = "The compressed EC2 cloud-init payload exceeds EC2's 16 KiB user-data limit."
    }
  }

  depends_on = [
    aws_route_table_association.public,
    aws_vpc_security_group_egress_rule.app,
    aws_s3_bucket_public_access_block.replica,
    aws_s3_bucket_server_side_encryption_configuration.replica,
    aws_iam_role_policy.app,
    aws_iam_role_policy_attachment.ssm,
  ]

  tags = { Name = "cairn-quick" }
}

resource "aws_eip" "app" {
  domain = "vpc"
}

resource "aws_eip_association" "app" {
  allocation_id = aws_eip.app.id
  instance_id   = aws_instance.app.id
}
