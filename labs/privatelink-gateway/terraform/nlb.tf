# Internal NLB in the central VPC. Apps keep one OTLP address; targets are EC2 instances, so the client
# IP is preserved and the security groups in ec2.tf match on the service VPC CIDRs.
#
# Cross-zone load balancing is on because the gateway is a single instance in one AZ: with it off, an
# NLB node in the other AZ would have no healthy target and traffic through the Transit Gateway that
# lands on that node would fail.

locals {
  nlb_listeners = merge(
    {
      "4317" = { port = 4317, target = "gateway", target_port = 4317 }
      "4318" = { port = 4318, target = "gateway", target_port = 4318 }
    },
    var.enable_kafka ? {
      "14317" = { port = 14317, target = "ingest", target_port = 4317 }
      "14318" = { port = 14318, target = "ingest", target_port = 4318 }
    } : {}
  )
}

resource "aws_lb" "otlp" {
  name                             = "${var.name_prefix}-otlp"
  load_balancer_type               = "network"
  internal                         = true
  subnets                          = [aws_subnet.this["central-0"].id, aws_subnet.this["central-1"].id]
  enable_cross_zone_load_balancing = true

  tags = { Name = "${var.name_prefix}-otlp" }
}

resource "aws_lb_target_group" "otlp" {
  for_each    = local.nlb_listeners
  name        = "${var.name_prefix}-${each.key}"
  port        = each.value.target_port
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = aws_vpc.this["central"].id

  # The collectors' health_check extension (13133) answers 200 on "/".
  health_check {
    protocol            = "HTTP"
    port                = "13133"
    path                = "/"
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  tags = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_lb_target_group_attachment" "otlp" {
  for_each         = local.nlb_listeners
  target_group_arn = aws_lb_target_group.otlp[each.key].arn
  target_id        = aws_instance.this[each.value.target].id
  port             = each.value.target_port
}

resource "aws_lb_listener" "otlp" {
  for_each          = local.nlb_listeners
  load_balancer_arn = aws_lb.otlp.arn
  port              = each.value.port
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.otlp[each.key].arn
  }
}
