import { ComponentType } from 'react'

interface StatsCardProps {
  name: string
  value: string
  change: string
  changeType: 'positive' | 'negative'
  icon: ComponentType<{ className?: string }>
}

export default function StatsCard({ name, value, change, changeType, icon: Icon }: StatsCardProps) {
  return (
    <div className="card">
      <div className="flex items-center">
        <div className="flex-shrink-0">
          <Icon className="h-8 w-8 text-primary-600" />
        </div>
        <div className="ml-5 w-0 flex-1">
          <dl>
            <dt className="text-sm font-medium text-gray-500 truncate">{name}</dt>
            <dd>
              <div className="text-lg font-medium text-gray-900">{value}</div>
            </dd>
          </dl>
        </div>
      </div>
      <div className="mt-4">
        <div className="flex items-center text-sm">
          <span className={`font-medium ${
            changeType === 'positive' ? 'text-green-600' : 'text-red-600'
          }`}>
            {change}
          </span>
          <span className="ml-2 text-gray-500">from last month</span>
        </div>
      </div>
    </div>
  )
}