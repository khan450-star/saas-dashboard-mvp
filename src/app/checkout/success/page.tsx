'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { CheckCircleIcon } from '@/components/Icons'

export default function CheckoutSuccessPage() {
  const [isLoading, setIsLoading] = useState(true)
  const [session, setSession] = useState(null)
  const searchParams = useSearchParams()
  const sessionId = searchParams.get('session_id')

  useEffect(() => {
    if (sessionId) {
      // In a real app, you might want to verify the session with Stripe
      // For now, we'll just simulate loading
      setTimeout(() => {
        setIsLoading(false)
      }, 1000)
    } else {
      setIsLoading(false)
    }
  }, [sessionId])

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600 mx-auto mb-4"></div>
          <p className="text-gray-600">Processing your payment...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8 text-center">
        <div>
          <CheckCircleIcon className="mx-auto h-16 w-16 text-green-500" />
          <h1 className="mt-6 text-3xl font-extrabold text-gray-900">
            Payment Successful!
          </h1>
          <p className="mt-2 text-gray-600">
            Thank you for your subscription. Your account has been upgraded.
          </p>
        </div>

        <div className="bg-white p-6 rounded-lg shadow border">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">What's next?</h2>
          <ul className="text-left space-y-2 text-gray-600">
            <li>• Your subscription is now active</li>
            <li>• You'll receive an email confirmation shortly</li>
            <li>• Access all premium features in your dashboard</li>
            <li>• Contact support if you have any questions</li>
          </ul>
        </div>

        <div className="flex flex-col sm:flex-row gap-4 justify-center">
          <Link
            href="/dashboard"
            className="btn-primary"
          >
            Go to Dashboard
          </Link>
          <Link
            href="/dashboard/billing"
            className="btn-secondary"
          >
            View Billing
          </Link>
        </div>

        {sessionId && (
          <div className="text-xs text-gray-500">
            Session ID: {sessionId}
          </div>
        )}
      </div>
    </div>
  )
}