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

    const subscription = await prisma.subscription.findFirst({
      where: { userId: session.user.id },
      orderBy: { createdAt: 'desc' },
    })

    // Transform the data for the frontend
    const transformedSubscription = subscription ? {
      id: subscription.id,
      status: subscription.status,
      plan: subscription.stripePriceId === 'price_starter' ? 'Starter' : 'Pro',
      currentPeriodStart: subscription.currentPeriodStart.toISOString(),
      currentPeriodEnd: subscription.currentPeriodEnd.toISOString(),
    } : null

    return NextResponse.json({ subscription: transformedSubscription })
  } catch (error) {
    console.error('Get subscription error:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}