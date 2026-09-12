terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags { tags = { Project = var.name, ManagedBy = "terraform", Purpose = "aiops-lab" } }
}
variable "account_id" { type = string }
variable "region" { default = "us-east-1" }
variable "name" { default = "capstone-aiops-lab" }
variable "kubernetes_version" { default = "1.35" }
variable "admin_principal_arn" { type = string }
variable "allowed_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.allowed_cidrs) > 0 && alltrue([for c in var.allowed_cidrs : can(cidrhost(c, 0)) && c != "0.0.0.0/0"])
    error_message = "Supply explicit client public CIDRs; unrestricted API access is not allowed."
  }
}
variable "ai_principal_arns" {
  type    = list(string)
  default = []
}
variable "executor_principal_arns" {
  type    = list(string)
  default = []
}
data "aws_availability_zones" "available" { state = "available" }
resource "aws_vpc" "lab" {
  cidr_block           = "10.84.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
}
resource "aws_internet_gateway" "lab" { vpc_id = aws_vpc.lab.id }
# Two public lab subnets avoid NAT gateways. Nodes have no SSH ingress;
# security groups still control traffic. Workloads are ClusterIP only.
resource "aws_subnet" "lab" {
  count                   = 2
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = cidrsubnet(aws_vpc.lab.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
}
resource "aws_route_table" "lab" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }
}
resource "aws_route_table_association" "lab" {
  count          = 2
  subnet_id      = aws_subnet.lab[count.index].id
  route_table_id = aws_route_table.lab.id
}
resource "aws_iam_role" "cluster" {
  name               = "${var.name}-cluster"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "eks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}
resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${var.name}/cluster"
  retention_in_days = 7
}
resource "aws_eks_cluster" "lab" {
  name                      = var.name
  role_arn                  = aws_iam_role.cluster.arn
  version                   = var.kubernetes_version
  enabled_cluster_log_types = ["api", "audit", "authenticator"]
  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }
  vpc_config {
    subnet_ids              = aws_subnet.lab[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.allowed_cidrs
  }
  depends_on = [aws_iam_role_policy_attachment.cluster, aws_cloudwatch_log_group.eks]
}
resource "aws_iam_role" "node" {
  name               = "${var.name}-node"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "node" {
  for_each   = toset(["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly", "AmazonEKS_CNI_Policy"])
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}
resource "aws_launch_template" "node" {
  name_prefix = "${var.name}-"
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 40
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }
}
resource "aws_eks_node_group" "lab" {
  cluster_name    = aws_eks_cluster.lab.name
  node_group_name = "lab"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.lab[*].id
  instance_types  = ["t3.large"]
  capacity_type   = "ON_DEMAND"
  launch_template {
    id      = aws_launch_template.node.id
    version = aws_launch_template.node.latest_version
  }
  scaling_config {
    desired_size = 2
    min_size     = 2
    max_size     = 2
  }
  update_config { max_unavailable = 1 }
  depends_on = [aws_iam_role_policy_attachment.node]
}
resource "aws_eks_access_entry" "admin" {
  cluster_name  = aws_eks_cluster.lab.name
  principal_arn = var.admin_principal_arn
}
resource "aws_eks_access_policy_association" "admin" {
  cluster_name  = aws_eks_cluster.lab.name
  principal_arn = aws_eks_access_entry.admin.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
}
locals {
  integrations = merge(
    length(var.ai_principal_arns) > 0 ? { investigator = var.ai_principal_arns } : {},
    length(var.executor_principal_arns) > 0 ? { remediator = var.executor_principal_arns } : {}
  )
}
resource "aws_iam_role" "integration" {
  for_each             = local.integrations
  name                 = "${var.name}-${each.key}"
  max_session_duration = 3600
  assume_role_policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { AWS = each.value }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "integration" {
  for_each = local.integrations
  role     = aws_iam_role.integration[each.key].name
  policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = aws_eks_cluster.lab.arn }] })
}
resource "aws_eks_access_entry" "integration" {
  for_each          = local.integrations
  cluster_name      = aws_eks_cluster.lab.name
  principal_arn     = aws_iam_role.integration[each.key].arn
  kubernetes_groups = ["lab:${each.key}"]
}
data "tls_certificate" "eks" { url = aws_eks_cluster.lab.identity[0].oidc[0].issuer }
resource "aws_iam_openid_connect_provider" "eks" {
  url             = aws_eks_cluster.lab.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]
}
resource "aws_iam_role" "ebs" {
  name               = "${var.name}-ebs"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }, Action = "sts:AssumeRoleWithWebIdentity", Condition = { StringEquals = { "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:sub" = "system:serviceaccount:kube-system:ebs-csi-controller-sa", "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:aud" = "sts.amazonaws.com" } } }] })
}
resource "aws_iam_role_policy_attachment" "ebs" {
  role       = aws_iam_role.ebs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}
resource "aws_eks_addon" "ebs" {
  cluster_name             = aws_eks_cluster.lab.name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.ebs.arn
  depends_on               = [aws_eks_node_group.lab, aws_iam_role_policy_attachment.ebs]
}
resource "aws_ecr_repository" "image" {
  for_each             = toset(["web", "cart", "catalog", "image-provider"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_eks_addon" "metrics" {
  cluster_name = aws_eks_cluster.lab.name
  addon_name   = "metrics-server"
  depends_on   = [aws_eks_node_group.lab]
}
output "cluster_name" { value = aws_eks_cluster.lab.name }
output "region" { value = var.region }
output "repositories" { value = { for k, v in aws_ecr_repository.image : k => v.repository_url } }
output "integration_roles" { value = { for k, v in aws_iam_role.integration : k => v.arn } }
