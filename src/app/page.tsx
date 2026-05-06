import Link from 'next/link'
import { ArrowRightIcon, CheckIcon } from '@/components/Icons'

const features = [
  {
    name: 'Analytics Dashboard',
    description: 'Get detailed insights into your business metrics with real-time data visualization.'
  },
  {
    name: 'Team Collaboration',
    description: 'Work together with your team members and manage permissions efficiently.'
  },
  {
    name: 'Advanced Reporting',
    description: 'Generate comprehensive reports and export data in multiple formats.'
  },
  {
    name: 'Secure & Reliable',
    description: 'Enterprise-grade security with 99.9% uptime guarantee.'
  }
]

const pricingPlans = [
  {
    name: 'Starter',
    price: '$29',
    period: '/month',
    features: [
      'Up to 5 team members',
      'Basic analytics',
      'Email support',
      '10GB storage'
    ]
  },
  {
    name: 'Pro',
    price: '$99',
    period: '/month',
    features: [
      'Up to 25 team members',
      'Advanced analytics',
      'Priority support',
      '100GB storage',
      'Custom integrations'
    ]
  }
]

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <header className="border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center py-6">
            <div className="flex items-center">
              <h1 className="text-2xl font-bold text-gray-900">SaaS Dashboard</h1>
            </div>
            <div className="flex items-center space-x-4">
              <Link href="/auth/signin" className="text-gray-500 hover:text-gray-900">
                Sign In
              </Link>
              <Link href="/auth/signup" className="btn-primary">
                Get Started
              </Link>
            </div>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section className="py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <h1 className="text-5xl font-bold text-gray-900 mb-6">
            Build Better Products with
            <span className="text-primary-600 block">Data-Driven Insights</span>
          </h1>
          <p className="text-xl text-gray-600 mb-8 max-w-3xl mx-auto">
            Get the tools you need to analyze, understand, and grow your business.
            Join thousands of companies already using our platform.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link href="/auth/signup" className="btn-primary text-lg px-8 py-3">
              Start Free Trial
              <ArrowRightIcon className="ml-2 h-5 w-5" />
            </Link>
            <Link href="#features" className="btn-secondary text-lg px-8 py-3">
              Learn More
            </Link>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="py-20 bg-gray-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl font-bold text-gray-900 mb-4">
              Everything you need to succeed
            </h2>
            <p className="text-lg text-gray-600">
              Powerful features designed to help you make better decisions
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
            {features.map((feature, index) => (
              <div key={index} className="card text-center">
                <h3 className="text-lg font-semibold text-gray-900 mb-2">
                  {feature.name}
                </h3>
                <p className="text-gray-600">{feature.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing Section */}
      <section className="py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-16">
            <h2 className="text-3xl font-bold text-gray-900 mb-4">
              Simple, transparent pricing
            </h2>
            <p className="text-lg text-gray-600">
              Choose the plan that works best for your team
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-4xl mx-auto">
            {pricingPlans.map((plan, index) => (
              <div key={index} className="card text-center">
                <h3 className="text-2xl font-bold text-gray-900 mb-2">{plan.name}</h3>
                <div className="mb-6">
                  <span className="text-4xl font-bold text-primary-600">{plan.price}</span>
                  <span className="text-gray-500">{plan.period}</span>
                </div>
                <ul className="space-y-3 mb-8">
                  {plan.features.map((feature, featureIndex) => (
                    <li key={featureIndex} className="flex items-center justify-center">
                      <CheckIcon className="h-5 w-5 text-green-500 mr-2" />
                      <span className="text-gray-600">{feature}</span>
                    </li>
                  ))}
                </ul>
                <Link href="/auth/signup" className="btn-primary w-full">
                  Get Started
                </Link>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-200 py-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center text-gray-600">
            <p>&copy; 2024 SaaS Dashboard. All rights reserved.</p>
          </div>
        </div>
      </footer>
    </div>
  )
}