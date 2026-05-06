'use client'

import { useState, useEffect } from 'react'
import { useSession } from 'next-auth/react'
import { useRouter } from 'next/navigation'
import DashboardShell from '@/components/DashboardShell'
import { CheckIcon } from '@/components/Icons'
import Toast from '@/components/Toast'

interface Subscription {
  id: string
  status: string
  currentPeriodStart: string
  currentPeriodEnd: string
  plan: string
}

interface Invoice {
  id: string
  amount: number
  status: string
  createdAt: string
}

const plans = [
  {
    name: 'Starter',
    price: 29,
    priceId: 'price_starter',
    features: [
      'Up to 5 team members',
      'Basic analytics',
      'Email support',
      '10GB storage'
    ]
  },
  {
    name: 'Pro',
    price: 99,
    priceId: 'price_pro',
    features: [
      'Up to 25 team members',
      'Advanced analytics',
      'Priority support',
      '100GB storage',
      'Custom integrations'
    ]
  }
]

export default function BillingPage() {
  const { data: session, status } = useSession()
  const router = useRouter()
  const [subscription, setSubscription] = useState<Subscription | null>(null)
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null)
  const [showToast, setShowToast] = useState(false)
  const [toastMessage, setToastMessage] = useState('')
  const [toastType, setToastType] = useState<'success' | 'error'>('success')

  useEffect(() => {
    if (status === 'unauthenticated') {
      router.push('/auth/signin')
    } else if (status === 'authenticated') {
      fetchBillingData()
    }
  }, [status, router])

  const fetchBillingData = async () => {
    try {
      const [subResponse, invoicesResponse] = await Promise.all([
        fetch('/api/subscription'),
        fetch('/api/invoices')
      ])

      if (subResponse.ok) {
        const subData = await subResponse.json()
        setSubscription(subData.subscription)
      }

      if (invoicesResponse.ok) {
        const invoicesData = await invoicesResponse.json()
        setInvoices(invoicesData.invoices)
      }
    } catch (error) {
      console.error('Error fetching billing data:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleUpgrade = async (priceId: string, planName: string) => {
    if (!process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY?.startsWith('pk_')) {
      setToastMessage('Stripe is not configured. This is a demo environment.')
      setToastType('error')
      setShowToast(true)
      return
    }

    setCheckoutLoading(priceId)

    try {
      const response = await fetch('/api/create-checkout-session', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          priceId,
          planName,
        }),
      })

      const data = await response.json()

      if (response.ok && data.url) {
        window.location.href = data.url
      } else {
        setToastMessage(data.error || 'Failed to create checkout session')
        setToastType('error')
        setShowToast(true)
      }
    } catch (error) {
      setToastMessage('Network error. Please try again.')
      setToastType('error')
      setShowToast(true)
    } finally {
      setCheckoutLoading(null)
    }
  }

  if (status === 'loading' || isLoading) {
    return (
      <DashboardShell>
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-500">Loading...</div>
        </div>
      </DashboardShell>
    )
  }

  return (
    <DashboardShell>
      <div className="max-w-4xl">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900">Billing</h1>
          <p className="mt-2 text-gray-600">
            Manage your subscription and billing information.
          </p>
        </div>

        {/* Current Subscription */}
        <div className="card mb-8">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Current Plan</h2>
          {subscription ? (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <div>
                  <h3 className="text-lg font-medium text-gray-900">{subscription.plan}</h3>
                  <p className="text-gray-600">Status: {subscription.status}</p>
                </div>
                <div className="text-right">
                  <p className="text-sm text-gray-500">Current period</p>
                  <p className="text-sm text-gray-900">
                    {new Date(subscription.currentPeriodStart).toLocaleDateString()} - 
                    {new Date(subscription.currentPeriodEnd).toLocaleDateString()}
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-gray-600">No active subscription</p>
          )}
        </div>

        {/* Available Plans */}
        <div className="card mb-8">
          <h2 className="text-xl font-semibold text-gray-900 mb-6">Available Plans</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {plans.map((plan) => {
              const isCurrentPlan = subscription?.plan === plan.name
              const isLoading = checkoutLoading === plan.priceId
              
              return (
                <div key={plan.name} className={`border rounded-lg p-6 ${isCurrentPlan ? 'border-primary-500 bg-primary-50' : 'border-gray-200'}`}>
                  <div className="flex justify-between items-center mb-4">
                    <h3 className="text-lg font-semibold text-gray-900">{plan.name}</h3>
                    {isCurrentPlan && (
                      <span className="bg-primary-600 text-white text-xs px-2 py-1 rounded-full">
                        Current
                      </span>
                    )}
                  </div>
                  <div className="mb-4">
                    <span className="text-3xl font-bold text-gray-900">${plan.price}</span>
                    <span className="text-gray-500 ml-1">/month</span>
                  </div>
                  <ul className="space-y-2 mb-6">
                    {plan.features.map((feature, index) => (
                      <li key={index} className="flex items-center text-sm text-gray-600">
                        <CheckIcon className="h-4 w-4 text-green-500 mr-2 flex-shrink-0" />
                        {feature}
                      </li>
                    ))}
                  </ul>
                  <button
                    onClick={() => handleUpgrade(plan.priceId, plan.name)}
                    disabled={isCurrentPlan || isLoading}
                    className="w-full btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isLoading ? 'Processing...' : isCurrentPlan ? 'Current Plan' : 'Upgrade'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>

        {/* Invoice History */}
        <div className="card">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Invoice History</h2>
          {invoices.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Invoice ID
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Amount
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Status
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Date
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {invoices.map((invoice) => (
                    <tr key={invoice.id}>
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-mono text-gray-900">
                        {invoice.id}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        ${(invoice.amount / 100).toFixed(2)}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className={`inline-flex px-2 py-1 text-xs font-semibold rounded-full ${
                          invoice.status === 'paid' 
                            ? 'bg-green-100 text-green-800' 
                            : 'bg-yellow-100 text-yellow-800'
                        }`}>
                          {invoice.status}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {new Date(invoice.createdAt).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-gray-600">No invoices found</p>
          )}
        </div>
      </div>

      {showToast && (
        <Toast
          message={toastMessage}
          type={toastType}
          onClose={() => setShowToast(false)}
        />
      )}
    </DashboardShell>
  )
}