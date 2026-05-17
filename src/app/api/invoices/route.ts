import { NextRequest, NextResponse } from 'next/server'
import { auth } from '@/lib/auth'
import prisma from '@/lib/prisma'

export async function GET(request: NextRequest) {
  try {
    const session = await auth()
    
    if (!session?.user?.id) {
      return NextResponse.json(
        { error: 'Unauthorized' },
        { status: 401 }
      )
    }

    // Get user's subscriptions first
    const subscriptions = await prisma.subscription.findMany({
      where: { userId: session.user.id },
      select: { id: true }
    })

    if (subscriptions.length === 0) {
      return NextResponse.json({ invoices: [] })
    }

    const subscriptionIds = subscriptions.map(sub => sub.id)

    const invoices = await prisma.invoice.findMany({
      where: {
        subscriptionId: {
          in: subscriptionIds
        }
      },
      orderBy: { createdAt: 'desc' },
      take: 10 // Limit to recent invoices
    })

    // Transform the data for the frontend
    const transformedInvoices = invoices.map(invoice => ({
      id: invoice.stripeInvoiceId,
      amount: invoice.amountPaid,
      status: invoice.status,
      createdAt: invoice.createdAt.toISOString(),
    }))

    return NextResponse.json({ invoices: transformedInvoices })
  } catch (error) {
    console.error('Get invoices error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}