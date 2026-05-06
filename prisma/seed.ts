import { PrismaClient } from '@prisma/client'
import bcrypt from 'bcryptjs'

const prisma = new PrismaClient()

async function main() {
  console.log('Start seeding...')
  
  // Create users
  const hashedPassword = await bcrypt.hash('password123', 12)
  
  const user1 = await prisma.user.upsert({
    where: { email: 'john.doe@example.com' },
    update: {},
    create: {
      name: 'John Doe',
      email: 'john.doe@example.com',
      hashedPassword,
    },
  })
  
  const user2 = await prisma.user.upsert({
    where: { email: 'jane.smith@example.com' },
    update: {},
    create: {
      name: 'Jane Smith',
      email: 'jane.smith@example.com',
      hashedPassword,
    },
  })
  
  const user3 = await prisma.user.upsert({
    where: { email: 'admin@example.com' },
    update: {},
    create: {
      name: 'Admin User',
      email: 'admin@example.com',
      hashedPassword,
    },
  })

  // Create subscriptions
  const subscription1 = await prisma.subscription.upsert({
    where: { stripeSubscriptionId: 'sub_demo_1' },
    update: {},
    create: {
      userId: user1.id,
      stripeCustomerId: 'cus_demo_1',
      stripeSubscriptionId: 'sub_demo_1',
      stripePriceId: 'price_pro',
      status: 'active',
      currentPeriodStart: new Date(),
      currentPeriodEnd: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000), // 30 days from now
    },
  })

  const subscription2 = await prisma.subscription.upsert({
    where: { stripeSubscriptionId: 'sub_demo_2' },
    update: {},
    create: {
      userId: user2.id,
      stripeCustomerId: 'cus_demo_2',
      stripeSubscriptionId: 'sub_demo_2',
      stripePriceId: 'price_starter',
      status: 'active',
      currentPeriodStart: new Date(Date.now() - 15 * 24 * 60 * 60 * 1000), // 15 days ago
      currentPeriodEnd: new Date(Date.now() + 15 * 24 * 60 * 60 * 1000), // 15 days from now
    },
  })

  // Create invoices
  await prisma.invoice.upsert({
    where: { stripeInvoiceId: 'in_demo_1' },
    update: {},
    create: {
      subscriptionId: subscription1.id,
      stripeInvoiceId: 'in_demo_1',
      amountPaid: 9900, // $99.00 in cents
      status: 'paid',
    },
  })

  await prisma.invoice.upsert({
    where: { stripeInvoiceId: 'in_demo_2' },
    update: {},
    create: {
      subscriptionId: subscription2.id,
      stripeInvoiceId: 'in_demo_2',
      amountPaid: 2900, // $29.00 in cents
      status: 'paid',
    },
  })

  await prisma.invoice.upsert({
    where: { stripeInvoiceId: 'in_demo_3' },
    update: {},
    create: {
      subscriptionId: subscription1.id,
      stripeInvoiceId: 'in_demo_3',
      amountPaid: 9900, // $99.00 in cents
      status: 'paid',
      createdAt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000), // 30 days ago
    },
  })

  await prisma.invoice.upsert({
    where: { stripeInvoiceId: 'in_demo_4' },
    update: {},
    create: {
      subscriptionId: subscription2.id,
      stripeInvoiceId: 'in_demo_4',
      amountPaid: 2900, // $29.00 in cents
      status: 'paid',
      createdAt: new Date(Date.now() - 45 * 24 * 60 * 60 * 1000), // 45 days ago
    },
  })

  console.log('Seeding finished.')
  console.log('Demo users created:')
  console.log('- john.doe@example.com (password: password123) - Pro plan')
  console.log('- jane.smith@example.com (password: password123) - Starter plan')
  console.log('- admin@example.com (password: password123)')
}

main()
  .catch((e) => {
    console.error(e)
    process.exit(1)
  })
  .finally(async () => {
    await prisma.$disconnect()
  })