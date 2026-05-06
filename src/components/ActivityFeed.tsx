interface Activity {
  id: number
  type: string
  message: string
  time: string
}

interface ActivityFeedProps {
  activities: Activity[]
}

const getActivityIcon = (type: string) => {
  switch (type) {
    case 'user_signup':
      return '👤'
    case 'payment':
      return '💳'
    case 'feature_usage':
      return '📊'
    case 'system':
      return '⚙️'
    case 'user_activity':
      return '👥'
    default:
      return '📝'
  }
}

const getActivityColor = (type: string) => {
  switch (type) {
    case 'user_signup':
      return 'bg-blue-100 text-blue-800'
    case 'payment':
      return 'bg-green-100 text-green-800'
    case 'feature_usage':
      return 'bg-purple-100 text-purple-800'
    case 'system':
      return 'bg-gray-100 text-gray-800'
    case 'user_activity':
      return 'bg-yellow-100 text-yellow-800'
    default:
      return 'bg-gray-100 text-gray-800'
  }
}

export default function ActivityFeed({ activities }: ActivityFeedProps) {
  return (
    <div className="card">
      <h3 className="text-lg font-semibold text-gray-900 mb-4">
        Recent Activity
      </h3>
      <div className="flow-root">
        <ul className="-mb-8">
          {activities.map((activity, activityIdx) => (
            <li key={activity.id}>
              <div className="relative pb-8">
                {activityIdx !== activities.length - 1 ? (
                  <span
                    className="absolute top-4 left-4 -ml-px h-full w-0.5 bg-gray-200"
                    aria-hidden="true"
                  />
                ) : null}
                <div className="relative flex space-x-3">
                  <div>
                    <span className={`h-8 w-8 rounded-full flex items-center justify-center text-sm ${getActivityColor(activity.type)}`}>
                      {getActivityIcon(activity.type)}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div>
                      <p className="text-sm text-gray-900">
                        {activity.message}
                      </p>
                      <p className="mt-0.5 text-xs text-gray-500">
                        {activity.time}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}