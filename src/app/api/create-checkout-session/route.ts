import { NextRequest, NextResponse } from 'next/server'
import { auth } from '@/lib/auth'
import Stripe from 'stripe'
import { z } from 'zod'

// Initialize Stripe only if we have a real key
let stripe: Stripe | null = null
if (process.env.STRIPE_SECRET_KEY?.startsWith('sk_')) {
  stripe = new Stripe(process.env.STRIPE_SECRET_KEY, {
    apiVersion: '2023-10-16',
  })
}

const checkoutSchema = z.object({
  priceId: z.string().min(1, 'Price ID is required'),
  planName: z.string().min(1, 'Plan name is required'),
})

export async function POST(request: NextRequest) {
  try {
    // Check if Stripe is configured
    if (!stripe) {
      return NextResponse.json(
        { error: 'Stripe is not configured. This is a demo environment.' },
        { status: 400 }
      )
    }

    const session = await auth()
    
    if (!session?.user?.id) {
      return NextResponse.json(
        { error: 'Unauthorized' },
        { status: 401 }
      )
    }

    const body = await request.json()
    const { priceId, planName } = checkoutSchema.parse(body)

    // Create Stripe checkout session
    const checkoutSession = await stripe.checkout.sessions.create({
      mode: 'subscription',
      payment_method_types: ['card'],
      line_items: [
        {
          price: priceId,
          quantity: 1,
        },
      ],
      success_url: `${process.env.NEXTAUTH_URL}/checkout/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${process.env.NEXTAUTH_URL}/checkout/cancel`,
      customer_email: session.user.email || undefined,
      metadata: {
        userId: session.user.id,
        planName,
      },
    })

    return NextResponse.json({ url: checkoutSession.url })
  } catch (error) {
    console.error('Create checkout session error:', error)
    
    if (error instanceof z.ZodError) {
      return NextResponse.json(
        { error: error.errors[0].message },
        { status: 400 }
      )
    }
    
    if (error instanceof Stripe.errors.StripeError) {
      return NextResponse.json(
        { error: error.message },
        { status: 400 }
      )
    }
    
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}