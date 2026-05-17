import { redirect } from 'next/navigation'
import { auth } from '@/lib/auth'
import DashboardShell from '@/components/DashboardShell'
import StatsCard from '@/components/StatsCard'
import ActivityFeed from '@/components/ActivityFeed'
import { BarChartIcon, TrendingUpIcon, UsersIcon, DollarSignIcon } from '@/components/Icons'

const stats = [
  {
    name: 'Total Revenue',
    value: '$45,231',
    change: '+20.1%',
    changeType: 'positive' as const,
    icon: DollarSignIcon,
  },
  {
    name: 'Active Users',
    value: '1,423',
    change: '+12.5%',
    changeType: 'positive' as const,
    icon: UsersIcon,
  },
  {
    name: 'Page Views',
    value: '89,432',
    change: '+4.3%',
    changeType: 'positive' as const,
    icon: BarChartIcon,
  },
  {
    name: 'Conversion Rate',
    value: '2.4%',
    change: '-1.2%',
    changeType: 'negative' as const,
    icon: TrendingUpIcon,
  },
]

const recentActivity = [
  {
    id: 1,
    type: 'user_signup',
    message: 'New user registered: john.doe@example.com',
    time: '2 minutes ago',
  },
  {
    id: 2,
    type: 'payment',
    message: 'Payment received: $99.00 from Pro plan subscriber',
    time: '15 minutes ago',
  },
  {
    id: 3,
    type: 'feature_usage',
    message: 'API endpoint /analytics called 1,234 times today',
    time: '1 hour ago',
  },
  {
    id: 4,
    type: 'system',
    message: 'Database backup completed successfully',
    time: '2 hours ago',
  },
  {
    id: 5,
    type: 'user_activity',
    message: '25 users active in the last hour',
    time: '3 hours ago',
  },
]

export default async function DashboardPage() {
  const session = await auth()
  
  if (!session) {
    redirect('/auth/signin')
  }

  return (
    <DashboardShell>
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">
          Welcome back, {session.user?.name || 'User'}!
        </h1>
        <p className="mt-2 text-gray-600">
          Here's what's happening with your business today.
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        {stats.map((stat) => (
          <StatsCard key={stat.name} {...stat} />
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Quick Actions */}
        <div className="lg:col-span-1">
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              Quick Actions
            </h3>
            <div className="space-y-3">
              <button className="w-full text-left p-3 rounded-lg bg-primary-50 hover:bg-primary-100 text-primary-700 transition-colors">
                Generate Report
              </button>
              <button className="w-full text-left p-3 rounded-lg bg-gray-50 hover:bg-gray-100 text-gray-700 transition-colors">
                Export Data
              </button>
              <button className="w-full text-left p-3 rounded-lg bg-gray-50 hover:bg-gray-100 text-gray-700 transition-colors">
                Invite Team Member
              </button>
              <button className="w-full text-left p-3 rounded-lg bg-gray-50 hover:bg-gray-100 text-gray-700 transition-colors">
                View Analytics
              </button>
            </div>
          </div>
        </div>

        {/* Recent Activity */}
        <div className="lg:col-span-2">
          <ActivityFeed activities={recentActivity} />
        </div>
      </div>
    </DashboardShell>
  )
}