import Link from 'next/link'
import { XCircleIcon } from '@/components/Icons'

export default function CheckoutCancelPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8 text-center">
        <div>
          <XCircleIcon className="mx-auto h-16 w-16 text-red-500" />
          <h1 className="mt-6 text-3xl font-extrabold text-gray-900">
            Payment Cancelled
          </h1>
          <p className="mt-2 text-gray-600">
            Your payment was cancelled. No charges were made to your account.
          </p>
        </div>

        <div className="bg-white p-6 rounded-lg shadow border">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">What happened?</h2>
          <ul className="text-left space-y-2 text-gray-600">
            <li>• You cancelled the payment process</li>
            <li>• No subscription was created</li>
            <li>• Your current plan remains unchanged</li>
            <li>• You can try again anytime</li>
          </ul>
        </div>

        <div className="flex flex-col sm:flex-row gap-4 justify-center">
          <Link
            href="/dashboard/billing"
            className="btn-primary"
          >
            Try Again
          </Link>
          <Link
            href="/dashboard"
            className="btn-secondary"
          >
            Back to Dashboard
          </Link>
        </div>

        <div className="text-sm text-gray-500">
          Need help? <a href="mailto:support@example.com" className="text-primary-600 hover:text-primary-500">Contact Support</a>
        </div>
      </div>
    </div>
  )
}