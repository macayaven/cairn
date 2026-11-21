output "public_ipv4" {
  description = "Set the domain's A record to this address."
  value       = aws_eip.app.public_ip
}

output "url" {
  value = "https://${var.domain}"
}

output "region" {
  value = var.region
}

output "backup_bucket" {
  value = aws_s3_bucket.replica.bucket
}

output "settings_secret" {
  description = "ARN to pass to Secrets Manager. Contains no secret value."
  value       = aws_secretsmanager_secret.settings.arn
}

output "connect_command" {
  description = "Run locally to open a Session Manager shell."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.app.id}"
}

output "first_key_command" {
  description = "Run on the VM after HTTPS works. The command prints the key file's path."
  value       = "sudo /opt/cairn/bootstrap.sh first-key"
}
