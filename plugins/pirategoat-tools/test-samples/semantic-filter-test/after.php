<?php

namespace App\Payment;

use App\Gateway\PaymentGateway;
use App\Database\Repository;
use App\Notification\EmailService;

/**
 * Payment processor class
 *
 * Handles payment processing with gateway integration
 */
class Payment
{
    /** @var Repository */
    private Repository $repository;

    /** @var EmailService */
    private EmailService $emailService;

    /** @var PaymentGateway */
    private PaymentGateway $gateway;

    /**
     * Constructor
     *
     * @param Repository $repository Data repository
     * @param EmailService $emailService Email notification service
     * @param PaymentGateway $gateway Payment gateway
     */
    public function __construct(
        Repository $repository,
        EmailService $emailService,
        PaymentGateway $gateway
    ) {
        $this->repository = $repository;
        $this->emailService = $emailService;
        $this->gateway = $gateway;
    }

    /**
     * Process a payment
     *
     * @param float $amount Amount to process
     * @return mixed Result from repository
     * @throws PaymentException If gateway charge fails
     */
    public function process($amount)
    {
        // Charge via gateway (NEW LOGIC)
        $chargeResult = $this->gateway->charge($amount);

        if (!$chargeResult->success) {
            throw new PaymentException('Charge failed');
        }

        $result = $this->repository->save([
            'amount' => $amount
        ]);

        $this->emailService->send('Payment processed');

        return $result;
    }
}
