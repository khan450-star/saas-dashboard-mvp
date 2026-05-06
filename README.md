# SaaS Dashboard MVP

A complete SaaS dashboard with user authentication, subscription management, and billing integration.

## Features

- **Authentication**: Email/password auth with NextAuth.js v5
- **Dashboard**: User analytics and activity feed
- **Settings**: Profile management
- **Billing**: Stripe integration for subscriptions
- **Responsive**: Mobile-first design with Tailwind CSS
- **Type-safe**: Full TypeScript implementation with Prisma ORM

## Tech Stack

- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS
- Prisma ORM (SQLite for development)
- NextAuth.js v5
- Stripe (Checkout + Webhooks)
- Zod (input validation)

## Getting Started

### Prerequisites

- Node.js 18+
- npm or yarn

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd saas-dashboard-mvp
   ```

2. **Install dependencies**
   ```bash
   npm install
   ```

3. **Set up the database**
   ```bash
   npx prisma generate
   npx prisma db push
   npx prisma db seed
   ```

4. **Start the development server**
   ```bash
   npm run dev
   ```

5. **Open your browser**
   Navigate to [http://localhost:3000](http://localhost:3000)

## Demo Users

After running the seed script, you can sign in with:

- **john.doe@example.com** (password: `password123`) - Pro plan subscriber
- **jane.smith@example.com** (password: `password123`) - Starter plan subscriber
- **admin@example.com** (password: `password123`) - No subscription

## Environment Variables

The app includes demo environment variables in `.env` and `.env.local`. For production, update these values:

```env
DATABASE_URL="file:./dev.db"  # Use PostgreSQL URL for production
NEXTAUTH_SECRET="your-secret-key"
NEXTAUTH_URL="https://your-domain.com"
STRIPE_SECRET_KEY="sk_test_your_stripe_secret_key"
STRIPE_PUBLISHABLE_KEY="pk_test_your_stripe_publishable_key"
STRIPE_WEBHOOK_SECRET="whsec_your_webhook_secret"
```

## Stripe Integration

### Development Setup

1. Create a Stripe account and get your API keys
2. Update the environment variables with your real Stripe keys
3. Install Stripe CLI: `https://stripe.com/docs/stripe-cli`
4. Forward webhooks to your local server:
   ```bash
   stripe listen --forward-to localhost:3000/api/webhooks/stripe
   ```
5. Update `STRIPE_WEBHOOK_SECRET` with the webhook signing secret

### Production Deployment

1. Set up webhook endpoint: `https://your-domain.com/api/webhooks/stripe`
2. Subscribe to these events:
   - `checkout.session.completed`
   - `invoice.payment_succeeded`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`

## Database Schema

The app uses the following main models:

- **User**: User accounts with authentication
- **Account/Session**: NextAuth.js session management
- **Subscription**: Stripe subscription data
- **Invoice**: Payment history

## Project Structure

```
src/
├── app/                    # Next.js App Router pages
│   ├── api/               # API routes
│   ├── auth/              # Authentication pages
│   ├── dashboard/         # Protected dashboard pages
│   └── checkout/          # Stripe checkout pages
├── components/            # Reusable UI components
└── lib/                   # Utility functions and configs
prisma/
├── schema.prisma         # Database schema
└── seed.ts              # Database seeding script
```

## Key Features

### Authentication
- Email/password registration and login
- Protected routes with middleware
- Session management with NextAuth.js v5

### Dashboard
- User statistics and metrics
- Activity feed with recent events
- Quick action buttons

### Settings
- Profile information updates
- Account information display
- Form validation with Zod

### Billing
- Subscription plan display
- Stripe Checkout integration
- Invoice history
- Webhook handling for subscription updates

## Security Features

- Input validation on all forms
- CSRF protection via NextAuth.js
- Webhook signature verification
- Password hashing with bcrypt
- SQL injection prevention with Prisma

## Development Commands

```bash
# Start development server
npm run dev

# Build for production
npm run build

# Start production server
npm start

# Database commands
npx prisma generate      # Generate Prisma client
npx prisma db push       # Push schema changes
npx prisma db seed       # Seed database
npx prisma studio        # Open database browser
```

## Deployment

This app is optimized for deployment on Vercel:

1. Push to GitHub
2. Connect repository to Vercel
3. Set environment variables in Vercel dashboard
4. Deploy

For PostgreSQL, consider:
- Vercel Postgres
- Supabase
- PlanetScale
- Railway

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

MIT License - see LICENSE file for details
